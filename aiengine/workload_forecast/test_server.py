#!/usr/bin/env python3
"""
Simple server test for cost-based index advisor
"""
import sys
import os
sys.path.insert(0, '/code/neurdb-dev/aiengine/workload_forecast/src')

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route('/test_cost_based', methods=['POST'])
def test_cost_based():
    """Test the cost-based index advisor"""
    try:
        from cost_based_index_advisor import CostBasedIndexAdvisor, IndexType

        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        # Database connection parameters for container
        db_params = {
            'host': 'localhost',
            'port': 5432,  # Container port
            'database': 'neurdb',
            'user': 'neurdb',
            'password': ''
        }

        # Sample queries
        workload_queries = data.get('workload_queries', [
            "SELECT * FROM movie_info WHERE movie_id = 12345",
            "SELECT * FROM cast_info WHERE person_id = 67890 AND role_id = 1",
            "SELECT * FROM title WHERE production_year > 2000 ORDER BY title"
        ])

        # Schema information
        schema_info = data.get('schema_info', {
            'movie_info': {'columns': ['movie_id', 'info_type_id', 'info'], 'size_mb': 500},
            'cast_info': {'columns': ['person_id', 'movie_id', 'role_id', 'note'], 'size_mb': 800},
            'title': {'columns': ['title_id', 'title', 'kind_id', 'production_year'], 'size_mb': 600}
        })

        # Initialize advisor
        advisor = CostBasedIndexAdvisor(db_params)

        # Run optimization with short time limit for testing
        result = advisor.recommend_indexes(
            workload_queries=workload_queries,
            schema_info=schema_info,
            time_limit_seconds=10  # Very short time for testing
        )

        # Prepare response
        response = {
            'status': 'success',
            'optimization_time_seconds': result.optimization_time,
            'final_benefit_score': result.best_solution.benefit_score,
            'recommended_indexes': []
        }

        # Serialize recommended indexes
        for i, index in enumerate(result.best_solution.indexes, 1):
            index_data = {
                'priority': i,
                'table_name': index.table_name,
                'columns': index.columns,
                'index_type': index.index_type.value,
                'estimated_size_mb': index.estimated_size_mb,
                'benefit_score': index.benefit_score,
                'sql_statement': f"CREATE INDEX idx_{index.table_name}_{'_'.join(index.columns)} ON {index.table_name} ({', '.join(index.columns)}) USING {index.index_type.value}"
            }
            response['recommended_indexes'].append(index_data)

        return jsonify(response)

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/')
def index():
    return jsonify({
        'service': 'NeurDB Cost-Based Index Advisor Test Server',
        'status': 'running',
        'endpoints': {
            'POST /test_cost_based': 'Test cost-based index advisor'
        }
    })

if __name__ == '__main__':
    print("Starting Cost-Based Index Advisor Test Server on port 8778")
    print("Endpoint: POST http://localhost:8778/test_cost_based")
    app.run(host='0.0.0.0', port=8778, debug=True)