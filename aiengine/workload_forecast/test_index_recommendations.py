#!/usr/bin/env python3
"""
Test script for Phase 4 Index Recommendation System
Tests the complete workflow from workload analysis to index recommendations
"""

import json
import logging
import asyncio
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
import requests
import time

# Add src directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/test_index_recommendations.log')
    ]
)
logger = logging.getLogger(__name__)

class IndexRecommendationTester:
    """Test class for index recommendation system"""

    def __init__(self, base_url="http://localhost:8777"):
        self.base_url = base_url
        self.session = requests.Session()
        self.test_results = []

    def log_test(self, test_name, status, details="", response_data=None):
        """Log test result"""
        result = {
            'test_name': test_name,
            'status': status,
            'timestamp': datetime.now().isoformat(),
            'details': details,
            'response_data': response_data
        }
        self.test_results.append(result)

        status_emoji = "✓" if status == "PASS" else "✗" if status == "FAIL" else "⚠"
        print(f"{status_emoji} {test_name}: {status}")
        if details:
            print(f"  Details: {details}")

    def make_request(self, endpoint, method="GET", data=None, params=None):
        """Make HTTP request to server"""
        url = f"{self.base_url}{endpoint}"

        try:
            if method == "GET":
                response = self.session.get(url, params=params)
            elif method == "POST":
                response = self.session.post(url, json=data)
            else:
                raise ValueError(f"Unsupported method: {method}")

            return response
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return None

    def test_server_health(self):
        """Test server health check"""
        response = self.make_request("/health")

        if response and response.status_code == 200:
            data = response.json()
            if data.get('status') == 'healthy':
                self.log_test("Server Health Check", "PASS", "Server is healthy")
                return True
            else:
                self.log_test("Server Health Check", "FAIL", f"Server status: {data.get('status')}")
                return False
        else:
            self.log_test("Server Health Check", "FAIL", "No response or error")
            return False

    def test_server_info(self):
        """Test server info endpoint"""
        response = self.make_request("/")

        if response and response.status_code == 200:
            data = response.json()
            endpoints = data.get('endpoints', {})

            # Check if Phase 4 endpoints are listed
            phase4_endpoints = [ep for ep in endpoints.keys() if 'index' in ep.lower()]

            if phase4_endpoints:
                self.log_test("Server Info - Phase 4 Endpoints", "PASS",
                            f"Found {len(phase4_endpoints)} index endpoints")
                return True
            else:
                self.log_test("Server Info - Phase 4 Endpoints", "FAIL",
                            "No Phase 4 endpoints found")
                return False
        else:
            self.log_test("Server Info", "FAIL", "No response or error")
            return False

    def test_workload_ingestion(self):
        """Test workload data ingestion"""
        sample_queries = [
            "SELECT * FROM movie_info WHERE movie_id = 123",
            "SELECT title FROM title WHERE production_year > 2000 ORDER BY title",
            "SELECT * FROM cast_info WHERE person_id = 456 AND role_id = 1",
            "SELECT COUNT(*) FROM movie_keyword WHERE keyword_id = 789",
            "SELECT name FROM person_name WHERE name LIKE 'John%' AND gender = 'm'",
            "SELECT * FROM movie_info WHERE info_type_id = 100 AND movie_id IN (1,2,3)",
            "SELECT DISTINCT movie_id FROM cast_info WHERE person_id = 123 ORDER BY movie_id"
        ]

        ingested_count = 0
        for query in sample_queries:
            data = {
                'sql': query,
                'timestamp': datetime.now().isoformat(),
                'log_file': '/tmp/test_workload.csv'
            }

            response = self.make_request("/ingest_query", "POST", data)

            if response and response.status_code == 200:
                result = response.json()
                if result.get('status') == 'logged':
                    ingested_count += 1
                else:
                    logger.warning(f"Failed to ingest query: {result.get('message')}")
            else:
                logger.error(f"Error ingesting query: {response.text if response else 'No response'}")

        if ingested_count == len(sample_queries):
            self.log_test("Workload Ingestion", "PASS",
                        f"Successfully ingested {ingested_count} queries")
            return True
        else:
            self.log_test("Workload Ingestion", "FAIL",
                        f"Only ingested {ingested_count}/{len(sample_queries)} queries")
            return False

    def test_workload_analysis(self):
        """Test workload analysis with clustering"""
        data = {
            'workload_data': '/tmp/test_workload.csv',
            'forecast_horizon_minutes': 120,
            'perform_index_analysis': False,  # Disable for this test
            'perform_clustering': True,
            'clustering_params': {
                'similarity_threshold': 0.7,
                'min_cluster_size': 2,
                'granularity_minutes': 1
            }
        }

        response = self.make_request("/analyze_workload", "POST", data)

        if response and response.status_code == 200:
            result = response.json()

            if result.get('status') == 'success':
                workload_summary = result.get('workload_summary', {})
                clustering_result = result.get('clustering_result', {})

                details = []
                details.append(f"Total queries: {workload_summary.get('total_queries', 0)}")
                details.append(f"Unique templates: {workload_summary.get('unique_templates', 0)}")
                details.append(f"Clusters found: {clustering_result.get('num_clusters', 0)}")

                self.log_test("Workload Analysis", "PASS", "; ".join(details))
                return True, result
            else:
                self.log_test("Workload Analysis", "FAIL", result.get('message', 'Unknown error'))
                return False, None
        else:
            error_msg = response.text if response else "No response"
            self.log_test("Workload Analysis", "FAIL", f"HTTP error: {error_msg}")
            return False, None

    def test_index_service_start(self):
        """Test starting index recommendation service"""
        data = {
            'database_host': 'localhost',
            'database_port': 5432,
            'database_name': 'neurdb',
            'database_user': 'postgres',
            'database_password': 'postgres',
            'mode': 'on_demand',
            'forecast_horizon_hours': 24,
            'recommendation_strategy': 'balanced',
            'max_recommendations': 20,
            'enable_hypothetical_analysis': False,  # Disable for initial test
            'lookback_hours': 24
        }

        response = self.make_request("/index/service/start", "POST", data)

        if response and response.status_code == 200:
            result = response.json()
            if result.get('status') == 'success':
                self.log_test("Index Service Start", "PASS",
                            "Service started successfully")
                return True
            else:
                self.log_test("Index Service Start", "FAIL", result.get('message'))
                return False
        else:
            error_msg = response.text if response else "No response"
            self.log_test("Index Service Start", "FAIL", f"HTTP error: {error_msg}")
            return False

    def test_index_service_status(self):
        """Test index service status"""
        response = self.make_request("/index/service/status")

        if response and response.status_code == 200:
            result = response.json()
            if result.get('status') == 'success':
                service_status = result.get('service_status', {})

                details = []
                details.append(f"Initialized: {service_status.get('initialized', False)}")
                details.append(f"Running: {service_status.get('running', False)}")
                details.append(f"Mode: {service_status.get('config', {}).get('mode', 'Unknown')}")

                self.log_test("Index Service Status", "PASS", "; ".join(details))
                return True
            else:
                self.log_test("Index Service Status", "FAIL", result.get('message'))
                return False
        else:
            error_msg = response.text if response else "No response"
            self.log_test("Index Service Status", "FAIL", f"HTTP error: {error_msg}")
            return False

    def test_index_recommendations_generate(self):
        """Test generating index recommendations"""
        data = {
            'start_time': (datetime.now() - timedelta(hours=1)).isoformat(),
            'end_time': datetime.now().isoformat(),
            'force_refresh': True,
            'forecast_horizon_hours': 24,
            'recommendation_strategy': 'balanced',
            'max_recommendations': 10,
            'enable_hypothetical_analysis': False,  # Disable for initial test
            'min_template_frequency': 1
        }

        response = self.make_request("/index/recommendations/generate", "POST", data)

        if response and response.status_code == 200:
            result = response.json()
            if result.get('status') == 'success':
                recommendations = result.get('recommendations', [])

                details = []
                details.append(f"Total recommendations: {len(recommendations)}")
                details.append(f"Templates analyzed: {result.get('total_templates_analyzed', 0)}")

                if recommendations:
                    # Get first recommendation details
                    first_rec = recommendations[0]
                    details.append(f"Top recommendation: {first_rec.get('table')}({', '.join(first_rec.get('columns', []))})")

                self.log_test("Index Recommendations Generate", "PASS", "; ".join(details))
                return True, result
            else:
                self.log_test("Index Recommendations Generate", "FAIL", result.get('message'))
                return False, None
        else:
            error_msg = response.text if response else "No response"
            self.log_test("Index Recommendations Generate", "FAIL", f"HTTP error: {error_msg}")
            return False, None

    def test_index_recommendations_export(self):
        """Test exporting index recommendations"""
        formats_to_test = ['json', 'sql']

        for format_type in formats_to_test:
            params = {'format': format_type}
            response = self.make_request("/index/recommendations/export", params=params)

            if response and response.status_code == 200:
                if format_type == 'json':
                    try:
                        data = response.json()
                        if 'recommendations' in data:
                            self.log_test(f"Export {format_type.upper()}", "PASS",
                                        f"Successfully exported {len(data.get('recommendations', []))} recommendations")
                        else:
                            self.log_test(f"Export {format_type.upper()}", "FAIL", "Invalid JSON format")
                            return False
                    except json.JSONDecodeError:
                        self.log_test(f"Export {format_type.upper()}", "FAIL", "Invalid JSON response")
                        return False
                elif format_type == 'sql':
                    content = response.text
                    if 'CREATE INDEX' in content:
                        self.log_test(f"Export {format_type.upper()}", "PASS",
                                    f"SQL script generated ({len(content)} chars)")
                    else:
                        self.log_test(f"Export {format_type.upper()}", "FAIL", "No CREATE INDEX statements found")
                        return False
            else:
                error_msg = response.text if response else "No response"
                self.log_test(f"Export {format_type.upper()}", "FAIL", f"HTTP error: {error_msg}")
                return False

        return True

    def test_comprehensive_workflow(self):
        """Test complete workflow from ingestion to recommendations"""
        print("\n" + "="*60)
        print("COMPREHENSIVE INDEX RECOMMENDATION WORKFLOW TEST")
        print("="*60)

        # Step 1: Health check
        if not self.test_server_health():
            return False

        # Step 2: Check server info
        if not self.test_server_info():
            return False

        # Step 3: Ingest test workload
        if not self.test_workload_ingestion():
            return False

        # Step 4: Analyze workload
        success, analysis_result = self.test_workload_analysis()
        if not success:
            return False

        # Step 5: Start index service
        if not self.test_index_service_start():
            return False

        # Step 6: Check service status
        if not self.test_index_service_status():
            return False

        # Step 7: Generate recommendations
        success, rec_result = self.test_index_recommendations_generate()
        if not success:
            return False

        # Step 8: Test export functionality
        if not self.test_index_recommendations_export():
            return False

        return True

    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*60)
        print("TEST SUMMARY")
        print("="*60)

        total_tests = len(self.test_results)
        passed_tests = len([r for r in self.test_results if r['status'] == 'PASS'])
        failed_tests = len([r for r in self.test_results if r['status'] == 'FAIL'])
        warning_tests = len([r for r in self.test_results if r['status'] == 'WARN'])

        print(f"Total Tests: {total_tests}")
        print(f"Passed: {passed_tests} ✓")
        print(f"Failed: {failed_tests} ✗")
        print(f"Warnings: {warning_tests} ⚠")

        success_rate = (passed_tests / total_tests) * 100 if total_tests > 0 else 0
        print(f"Success Rate: {success_rate:.1f}%")

        if failed_tests > 0:
            print("\nFailed Tests:")
            for result in self.test_results:
                if result['status'] == 'FAIL':
                    print(f"  ✗ {result['test_name']}: {result['details']}")

        if warning_tests > 0:
            print("\nWarnings:")
            for result in self.test_results:
                if result['status'] == 'WARN':
                    print(f"  ⚠ {result['test_name']}: {result['details']}")

        print("="*60)

        return success_rate >= 80  # Consider successful if 80%+ pass rate


def main():
    """Main test function"""
    print("Phase 4 Index Recommendation System Test")
    print("======================================")

    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Test Phase 4 Index Recommendation System')
    parser.add_argument('--base-url', default='http://localhost:8777',
                       help='Base URL for the AI engine server')
    parser.add_argument('--single-test', choices=[
        'health', 'info', 'ingestion', 'analysis', 'service-start',
        'service-status', 'recommendations', 'export'
    ], help='Run a single test instead of the full workflow')

    args = parser.parse_args()

    # Create tester
    tester = IndexRecommendationTester(args.base_url)

    try:
        if args.single_test:
            # Run single test
            if args.single_test == 'health':
                tester.test_server_health()
            elif args.single_test == 'info':
                tester.test_server_info()
            elif args.single_test == 'ingestion':
                tester.test_workload_ingestion()
            elif args.single_test == 'analysis':
                tester.test_workload_analysis()
            elif args.single_test == 'service-start':
                tester.test_index_service_start()
            elif args.single_test == 'service-status':
                tester.test_index_service_status()
            elif args.single_test == 'recommendations':
                tester.test_index_recommendations_generate()
            elif args.single_test == 'export':
                tester.test_index_recommendations_export()
        else:
            # Run comprehensive workflow
            success = tester.test_comprehensive_workflow()

            # Print summary
            test_passed = tester.print_summary()

            if success and test_passed:
                print("\n🎉 All tests passed! Phase 4 Index Recommendation System is working correctly.")
                return 0
            else:
                print("\n❌ Some tests failed. Please check the logs above.")
                return 1

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        return 2
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        logger.exception("Unexpected error during testing")
        return 3


if __name__ == "__main__":
    sys.exit(main())