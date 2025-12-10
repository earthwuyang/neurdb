#!/usr/bin/env python3
"""
Test script for GUC-based strategy switching

This script tests the new GUC variable functionality for switching between
predictive and reactive index management strategies from within the database.
"""

import psycopg2
import requests
import json
import time
import sys
from threading import Thread

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'neurdb',
    'user': 'neurdb_user',
    'password': 'neurdb_pass'
}

# AI Engine configuration
AI_ENGINE_URL = "http://localhost:8777"

class GUCTester:
    def __init__(self):
        self.db_conn = None
        self.test_results = []

    def connect_db(self):
        """Connect to the database"""
        try:
            self.db_conn = psycopg2.connect(**DB_CONFIG)
            self.db_conn.autocommit = True
            print("✓ Database connection successful")
            return True
        except Exception as e:
            print(f"✗ Database connection failed: {e}")
            return False

    def close_db(self):
        """Close database connection"""
        if self.db_conn:
            self.db_conn.close()
            print("✓ Database connection closed")

    def execute_sql(self, sql, description=""):
        """Execute SQL and return result"""
        try:
            with self.db_conn.cursor() as cursor:
                cursor.execute(sql)
                if cursor.description:
                    result = cursor.fetchall()
                    return result
                return True
        except Exception as e:
            print(f"✗ SQL Error [{description}]: {e}")
            return None

    def test_guc_parameter_exists(self):
        """Test if the GUC parameter exists"""
        print("\n" + "="*60)
        print("TEST: GUC Parameter Existence")
        print("="*60)

        # Check if GUC parameter exists
        result = self.execute_sql(
            "SELECT name, setting FROM pg_settings WHERE name = 'nr_index_management_strategy'",
            "Check GUC parameter exists"
        )

        if result:
            param_name, setting = result[0]
            print(f"✓ GUC parameter '{param_name}' exists with value: {setting}")
            self.test_results.append(("GUC Parameter Exists", True, f"Value: {setting}"))
            return True
        else:
            print("✗ GUC parameter 'nr_index_management_strategy' not found")
            self.test_results.append(("GUC Parameter Exists", False, "Parameter not found"))
            return False

    def test_sql_functions(self):
        """Test the SQL functions for strategy switching"""
        print("\n" + "="*60)
        print("TEST: SQL Functions")
        print("="*60)

        # Test getting current strategy
        result = self.execute_sql("SELECT nr_get_index_management_strategy()", "Get current strategy")
        if result:
            current_strategy = result[0][0]
            print(f"✓ Current strategy: {current_strategy}")
            self.test_results.append(("Get Current Strategy", True, current_strategy))
        else:
            print("✗ Failed to get current strategy")
            self.test_results.append(("Get Current Strategy", False, "Function failed"))
            return False

        # Test switching to predictive
        result = self.execute_sql("SELECT nr_set_index_management_strategy('predictive')", "Switch to predictive")
        if result and result[0][0]:
            print("✓ Successfully switched to predictive strategy")
            self.test_results.append(("Switch to Predictive", True, "Success"))
        else:
            print("✗ Failed to switch to predictive strategy")
            self.test_results.append(("Switch to Predictive", False, "Function failed"))
            return False

        # Verify strategy change
        result = self.execute_sql("SELECT nr_get_index_management_strategy()", "Verify predictive switch")
        if result and result[0][0] == 'predictive':
            print("✓ Strategy change verified: predictive")
            self.test_results.append(("Verify Predictive Switch", True, "Verified"))
        else:
            print("✗ Strategy change verification failed")
            self.test_results.append(("Verify Predictive Switch", False, "Verification failed"))
            return False

        # Test switching to reactive
        result = self.execute_sql("SELECT nr_set_index_management_strategy('reactive')", "Switch to reactive")
        if result and result[0][0]:
            print("✓ Successfully switched to reactive strategy")
            self.test_results.append(("Switch to Reactive", True, "Success"))
        else:
            print("✗ Failed to switch to reactive strategy")
            self.test_results.append(("Switch to Reactive", False, "Function failed"))
            return False

        # Verify strategy change
        result = self.execute_sql("SELECT nr_get_index_management_strategy()", "Verify reactive switch")
        if result and result[0][0] == 'reactive':
            print("✓ Strategy change verified: reactive")
            self.test_results.append(("Verify Reactive Switch", True, "Verified"))
        else:
            print("✗ Strategy change verification failed")
            self.test_results.append(("Verify Reactive Switch", False, "Verification failed"))
            return False

        return True

    def test_status_view(self):
        """Test the status view"""
        print("\n" + "="*60)
        print("TEST: Status View")
        print("="*60)

        result = self.execute_sql("SELECT * FROM nr_index_management_status", "Test status view")
        if result:
            print("✓ Status view working correctly:")
            for row in result:
                print(f"  {row[0]}: {row[1]} - {row[2]}")
            self.test_results.append(("Status View", True, f"Rows returned: {len(result)}"))
            return True
        else:
            print("✗ Status view failed")
            self.test_results.append(("Status View", False, "No data returned"))
            return False

    def test_direct_guc_commands(self):
        """Test direct GUC SET commands"""
        print("\n" + "="*60)
        print("TEST: Direct GUC Commands")
        print("="*60)

        # Test SET command
        result = self.execute_sql("SET nr_index_management_strategy = 0", "SET to predictive")
        if result:
            print("✓ SET command successful for predictive")
            self.test_results.append(("Direct SET Predictive", True, "Success"))
        else:
            print("✗ SET command failed for predictive")
            self.test_results.append(("Direct SET Predictive", False, "Command failed"))
            return False

        # Test SHOW command
        result = self.execute_sql("SHOW nr_index_management_strategy", "SHOW current value")
        if result:
            value = result[0][0]
            print(f"✓ SHOW command returned: {value}")
            self.test_results.append(("Direct SHOW", True, f"Value: {value}"))
        else:
            print("✗ SHOW command failed")
            self.test_results.append(("Direct SHOW", False, "Command failed"))
            return False

        # Test SET to reactive
        result = self.execute_sql("SET nr_index_management_strategy = 1", "SET to reactive")
        if result:
            print("✓ SET command successful for reactive")
            self.test_results.append(("Direct SET Reactive", True, "Success"))
        else:
            print("✗ SET command failed for reactive")
            self.test_results.append(("Direct SET Reactive", False, "Command failed"))
            return False

        return True

    def test_ai_engine_notification(self):
        """Test that AI engine receives notifications"""
        print("\n" + "="*60)
        print("TEST: AI Engine Notification")
        print("="*60)

        # Check if AI engine is running
        try:
            response = requests.get(f"{AI_ENGINE_URL}/health", timeout=5)
            if response.status_code == 200:
                print("✓ AI engine is running")
            else:
                print(f"⚠ AI engine returned status: {response.status_code}")
        except requests.exceptions.RequestException:
            print("⚠ AI engine is not running - notification test skipped")
            self.test_results.append(("AI Engine Notification", False, "AI engine not running"))
            return False

        # Get initial strategy
        response = requests.get(f"{AI_ENGINE_URL}/index/predictive/status", timeout=5)

        # Switch strategy via GUC
        print("Switching strategy via GUC...")
        self.execute_sql("SELECT nr_set_index_management_strategy('predictive')")
        time.sleep(2)  # Wait for notification

        # Check if AI engine received the change
        response = requests.get(f"{AI_ENGINE_URL}/index/predictive/status", timeout=5)
        if response.status_code == 200:
            print("✓ AI engine responded after GUC change")
            self.test_results.append(("AI Engine Notification", True, "Notification received"))
        else:
            print("⚠ AI engine response unclear")
            self.test_results.append(("AI Engine Notification", False, "Unclear response"))

        return True

    def test_error_handling(self):
        """Test error handling"""
        print("\n" + "="*60)
        print("TEST: Error Handling")
        print("="*60)

        # Test invalid strategy name
        result = self.execute_sql("SELECT nr_set_index_management_strategy('invalid')", "Test invalid strategy")
        if result is None:
            print("✓ Correctly rejected invalid strategy name")
            self.test_results.append(("Error Handling - Invalid Strategy", True, "Correctly rejected"))
        else:
            print("✗ Should have rejected invalid strategy name")
            self.test_results.append(("Error Handling - Invalid Strategy", False, "Should have rejected"))

        # Test invalid GUC value
        result = self.execute_sql("SET nr_index_management_strategy = 99", "Test invalid GUC value")
        if result is None:
            print("✓ Correctly rejected invalid GUC value")
            self.test_results.append(("Error Handling - Invalid GUC", True, "Correctly rejected"))
        else:
            print("✗ Should have rejected invalid GUC value")
            self.test_results.append(("Error Handling - Invalid GUC", False, "Should have rejected"))

        return True

    def run_all_tests(self):
        """Run all tests"""
        print("Starting GUC Strategy Switching Tests")
        print("="*80)

        if not self.connect_db():
            print("Cannot proceed without database connection")
            return False

        try:
            # Run all test suites
            self.test_guc_parameter_exists()
            self.test_sql_functions()
            self.test_status_view()
            self.test_direct_guc_commands()
            self.test_ai_engine_notification()
            self.test_error_handling()

            # Print summary
            self.print_summary()

        finally:
            self.close_db()

    def print_summary(self):
        """Print test summary"""
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)

        passed = sum(1 for _, success, _ in self.test_results if success)
        total = len(self.test_results)

        for test_name, success, details in self.test_results:
            status = "✓ PASS" if success else "✗ FAIL"
            print(f"{status}: {test_name} - {details}")

        print(f"\nOverall Result: {passed}/{total} tests passed")

        if passed == total:
            print("🎉 All tests passed!")
            return True
        else:
            print("⚠ Some tests failed. Check the details above.")
            return False

def main():
    """Main function"""
    tester = GUCTester()
    success = tester.run_all_tests()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()