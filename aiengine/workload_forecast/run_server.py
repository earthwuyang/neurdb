#!/usr/bin/env python3
"""
AI Engine for Workload Forecasting and Index Recommendation
HTTP server to receive workload data and return analysis
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
import numpy as np

# Add src directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

try:
    from templatizer import AdvancedTemplatizer, load_workload_from_csv
    from workload_clusterer import OnlineWorkloadClusterer
    from workload_store import WorkloadStore
    from forecasting_pipeline import ForecastingPipeline
    from forecast_index_service import ForecastIndexService, ForecastIndexConfig, ServiceMode
    from index_advisor import IndexCandidate, QueryAnalysis
    from index_recommendation_engine import RecommendationConfig, RecommendationStrategy
except ImportError as e:
    print(f"Error importing modules: {e}")
    print("Make sure all Phase 2, Phase 3, and Phase 4 modules are in the src/ directory")
    sys.exit(1)

# Flask should be available from the existing environment
try:
    from flask import Flask, request, jsonify
    from flask import has_request_context
except ImportError as e:
    print(f"Error: Flask is required but not available in the environment: {e}")
    print("The existing miniconda3 environment should have Flask installed.")
    exit(1)

# Try to import CORS, but it's optional
try:
    from flask_cors import CORS
    CORS_AVAILABLE = True
except ImportError:
    print("Warning: flask-cors not available, CORS disabled")
    print("To enable CORS support, install with: pip install flask-cors")
    CORS_AVAILABLE = False

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/code/neurdb-dev/aiengine/workload_forecast/workload_forecast_server.log')
    ]
)
logger = logging.getLogger(__name__)

# Global variables
server_start_time = datetime.now()
request_count = 0

# Phase 2 Clustering Components
templatizer = None
clusterer = None
workload_store = None

# Phase 3 Forecasting Components
forecasting_pipeline = None

# Phase 4 Index Recommendation Components
forecast_index_service = None

app = Flask(__name__)

# Enable CORS if available
if CORS_AVAILABLE:
    CORS(app)  # Enable CORS for cross-origin requests
    print("✓ CORS enabled")
else:
    print("⚠ CORS disabled (install flask-cors for CORS support)")


def load_workload_data(log_file_path):
    """Load and parse workload data from CSV file"""
    workload_data = []

    if not os.path.exists(log_file_path):
        logger.warning(f"Workload log file not found: {log_file_path}")
        return workload_data

    try:
        with open(log_file_path, 'r') as f:
            lines = f.readlines()

        # Skip header
        if lines and 'timestamp' in lines[0]:
            lines = lines[1:]

        logger.info(f"Loaded {len(lines)} workload entries from {log_file_path}")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse CSV: timestamp,template_hash,template,original_query
            parts = line.split(',', 3)
            if len(parts) >= 4:
                workload_data.append({
                    'timestamp': parts[0],
                    'template_hash': parts[1],
                    'template': parts[2].strip('"'),
                    'original_query': parts[3].strip('"')
                })

    except Exception as e:
        logger.error(f"Error loading workload data: {e}")

    return workload_data


def analyze_workload_template(workload_data):
    """Analyze workload and extract statistics"""
    if not workload_data:
        return {
            'total_queries': 0,
            'unique_templates': 0,
            'clusters': 0,
            'sample_templates': []
        }

    # Extract unique templates
    templates = {}
    for entry in workload_data:
        template_hash = entry['template_hash']
        if template_hash not in templates:
            templates[template_hash] = {
                'hash': template_hash,
                'text': entry['template'],
                'count': 0
            }
        templates[template_hash]['count'] += 1

    # Sort templates by frequency
    sorted_templates = sorted(
        templates.values(),
        key=lambda x: x['count'],
        reverse=True
    )

    return {
        'total_queries': len(workload_data),
        'unique_templates': len(sorted_templates),
        'clusters': min(8, len(sorted_templates)),  # Simple clustering placeholder
        'sample_templates': sorted_templates[:5]  # Top 5 templates
    }


def forecast_workload(workload_data, horizon_minutes):
    """Forecast future workload (placeholder implementation)"""
    if not workload_data:
        return {
            'predicted_queries': 0,
            'forecast_interval_minutes': horizon_minutes,
            'confidence': 0.0
        }

    # Simple forecasting: average recent activity
    recent_queries = len(workload_data)
    avg_per_hour = recent_queries / 24  # Assume 24 hours of data

    predicted_queries = int(avg_per_hour * (horizon_minutes / 60))
    confidence = 0.5  # Placeholder confidence

    return {
        'predicted_queries': predicted_queries,
        'forecast_interval_minutes': horizon_minutes,
        'confidence': confidence
    }


def recommend_indexes(workload_data):
    """Recommend indexes based on workload (placeholder implementation)"""
    recommendations = []

    if not workload_data:
        return recommendations

    # Simple analysis: look for WHERE clauses on common tables
    table_column_freq = {}

    for entry in workload_data:
        template = entry['template']
        if 'WHERE' in template.upper():
            # Extract table and column patterns (simplified)
            # In real implementation, use SQL parser

            # Check for common patterns
            if 'movie_id' in template:
                table_column_freq[('movie_info', 'movie_id')] = \
                    table_column_freq.get(('movie_info', 'movie_id'), 0) + 1

            if 'person_id' in template:
                table_column_freq[('person_info', 'person_id')] = \
                    table_column_freq.get(('person_info', 'person_id'), 0) + 1

    # Generate recommendations for frequent patterns
    for (table, column), freq in table_column_freq.items():
        if freq > 5:  # Threshold
            recommendations.append({
                'table': table,
                'columns': [column],
                'index_type': 'btree',
                'benefit_score': min(1.0, freq / 20.0),  # Normalize to 0-1
                'estimated_size_mb': 10.0,
                'sql': f"CREATE INDEX idx_{table}_{column} ON {table}({column});"
            })

    return recommendations[:5]  # Limit recommendations


@app.route('/')
def index():
    """Server info endpoint"""
    global request_count
    uptime = datetime.now() - server_start_time

    return jsonify({
        'service': 'NeurDB Workload Forecast AI Engine',
        'version': '1.0.0',
        'status': 'running',
        'uptime_seconds': int(uptime.total_seconds()),
        'requests_processed': request_count,
        'endpoints': {
            'GET /': 'This info',
            'POST /analyze_workload': 'Analyze workload with clustering (Phase 2)',
            'POST /ingest_query': 'Ingest a single query',
            'GET /health': 'Health check',
            'GET /clusters': 'Get all cluster information',
            'GET /clusters/<id>': 'Get detailed cluster information',
            'GET /templates/<hash>': 'Get template details',
            'GET /workload_timeseries': 'Get workload time series data',
            'Phase 3 Forecasting:': 'Workload forecasting endpoints',
            'POST /forecast/models/train': 'Train forecasting models',
            'POST /forecast/generate': 'Generate forecasts',
            'GET /forecast/models': 'List available models',
            'POST /forecast/models/<model_key>/evaluate': 'Evaluate model performance',
            'GET /forecast/status': 'Get forecasting pipeline status',
            'Phase 4 Index Recommendations:': 'Advanced index recommendation endpoints',
            'POST /index/recommendations/generate': 'Generate comprehensive index recommendations',
            'GET /index/recommendations/latest': 'Get latest recommendations',
            'GET /index/recommendations/export': 'Export recommendations in various formats',
            'GET /index/recommendations/history': 'Get recommendation history',
            'POST /index/service/start': 'Start scheduled/index service',
            'GET /index/service/status': 'Get service status',
            'POST /index/service/stop': 'Stop service',
            'Cost-Based Optimization:': 'DTA-inspired cost-based index optimization',
            'POST /index/recommendations/cost_based': 'Generate cost-based index recommendations using hypopg',
            'GET /index/recommendations/cost_based/compare': 'Compare pattern-based vs cost-based approaches'
        }
    })


@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat()
    })


@app.route('/analyze_workload', methods=['POST'])
def analyze_workload():
    """Analyze workload data with Phase 2 clustering and return recommendations"""
    global request_count, templatizer, clusterer, workload_store
    request_count += 1

    try:
        # Parse request
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        log_file_path = data.get('workload_data')
        forecast_horizon = data.get('forecast_horizon_minutes', 60)
        perform_index_analysis = data.get('perform_index_analysis', True)
        perform_clustering = data.get('perform_clustering', True)
        clustering_params = data.get('clustering_params', {})

        logger.info(f"Received analysis request for {log_file_path}, horizon: {forecast_horizon}min, clustering: {perform_clustering}")

        # Initialize components if not already done
        if templatizer is None:
            templatizer = AdvancedTemplatizer(
                granularity_minutes=clustering_params.get('granularity_minutes', 1),
                similarity_threshold=clustering_params.get('similarity_threshold', 0.8),
                enable_logical_features=clustering_params.get('enable_logical_features', True)
            )
            logger.info("Initialized AdvancedTemplatizer")

        if clusterer is None:
            clusterer = OnlineWorkloadClusterer(
                similarity_threshold=clustering_params.get('similarity_threshold', 0.8),
                min_cluster_size=clustering_params.get('min_cluster_size', 3),
                time_window_hours=clustering_params.get('time_window_hours', 24),
                similarity_window_days=clustering_params.get('similarity_window_days', 30),
                enable_knn_acceleration=clustering_params.get('enable_knn_acceleration', True),
                cluster_merge_threshold=clustering_params.get('cluster_merge_threshold', 0.9)
            )
            logger.info("Initialized OnlineWorkloadClusterer")

        # Initialize forecasting pipeline if not already done
        if forecasting_pipeline is None:
            forecasting_config = {
                'models_dir': 'models/forecasting',
                'retrain_interval_hours': 24,
                'min_training_samples': 50,
                'validation_split': 0.2,
                'enable_auto_retraining': True,
                'database': {
                    'host': 'localhost',
                    'port': 15432,
                    'database': 'neurdb_metrics',
                    'user': 'neurdb',
                    'password': ''
                },
                'model_configs': {
                    'ar': {
                        'max_order': 8,
                        'auto_order': True,
                        'seasonal': True
                    },
                    'rnn': {
                        'hidden_size': 64,
                        'num_layers': 2,
                        'sequence_length': 24,
                        'epochs': 50,
                        'batch_size': 16
                    },
                    'spectral': {
                        'n_harmonics': 15,
                        'frequency_threshold': 0.05,
                        'detrend_method': 'linear'
                    },
                    'ensemble': {
                        'models': ['ar', 'rnn', 'spectral'],
                        'weight_optimization': 'performance',
                        'performance_window': 20
                    }
                }
            }

            try:
                forecasting_pipeline = ForecastingPipeline(forecasting_config)
                logger.info("Initialized ForecastingPipeline")
            except Exception as e:
                logger.warning(f"Failed to initialize forecasting pipeline: {e}")
                forecasting_pipeline = None

        # Load workload data using advanced templatizer
        query_data, time_series = load_workload_from_csv(log_file_path, templatizer)

        if not query_data:
            return jsonify({'error': 'No valid workload data found'}), 400

        # Compute template statistics
        template_stats = templatizer.compute_template_statistics(time_series)

        # Perform clustering if requested
        clustering_result = None
        if perform_clustering and len(time_series) >= 2:
            clustering_result = clusterer.cluster_templates(time_series)
            logger.info(f"Clustering complete: {clustering_result['num_clusters']} clusters")

        # Store results in database if available
        if workload_store:
            try:
                # Store templates
                for entry in query_data:
                    template_hash = entry['template_hash']
                    template_info = templatizer.extract_logical_features(entry['original_query'], entry['template'])
                    workload_store.store_query_template(template_hash, entry['template'], template_info)

                # Store clustering session
                session_id = workload_store.create_clustering_session(
                    algorithm='ONLINE_COSINE',
                    parameters=clustering_params,
                    input_templates_count=len(time_series)
                )

                if clustering_result:
                    # Store cluster assignments
                    for template_hash, cluster_id in clustering_result['cluster_assignments'].items():
                        # Get similarity score from cluster metadata if available
                        similarity_score = 0.0
                        if cluster_id in clustering_result.get('cluster_info', {}):
                            cluster_info = clustering_result['cluster_info'][cluster_id]
                            # Use cluster quality as proxy for similarity
                            similarity_score = cluster_info.get('total_queries', 0) / max(1, cluster_info.get('size', 1))

                        workload_store.store_cluster_assignment(
                            template_hash, cluster_id, similarity_score, 0.8, 'CLUSTERING'
                        )

                    # Complete clustering session
                    workload_store.complete_clustering_session(
                        session_id=session_id,
                        output_clusters_count=clustering_result['num_clusters'],
                        unassigned_count=len(clustering_result['unassigned_templates']),
                        merged_count=0,  # TODO: Track merges
                        new_count=clustering_result['num_clusters'],  # Approximation
                        duration_ms=0,  # TODO: Track duration
                        quality_metrics=clustering_result.get('clustering_metadata', {})
                    )

            except Exception as db_error:
                logger.warning(f"Database storage failed: {db_error}")

        # Legacy workload analysis for compatibility
        workload_summary = analyze_workload_template(query_data)

        # Enhanced summary with clustering results
        enhanced_summary = workload_summary.copy()
        if clustering_result:
            enhanced_summary.update({
                'clustering': {
                    'num_clusters': clustering_result['num_clusters'],
                    'assigned_templates': len(clustering_result['cluster_assignments']),
                    'unassigned_templates': len(clustering_result['unassigned_templates']),
                    'clustering_metadata': clustering_result['clustering_metadata']
                }
            })

        # Forecast future workload (placeholder - will be enhanced in Phase 3)
        forecast = forecast_workload(query_data, forecast_horizon)

        # Generate index recommendations (placeholder - will be enhanced in Phase 4)
        recommendations = []
        if perform_index_analysis:
            recommendations = recommend_indexes(query_data)

        # Prepare response
        response = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'request_id': f"req_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{request_count}",
            'workload_summary': enhanced_summary,
            'forecast': forecast,
            'index_recommendations': recommendations,
            'clustering_result': clustering_result,
            'template_statistics': template_stats,
            'model_info': {
                'cluster_version': '2.0',  # Phase 2 clustering
                'forecast_models': ['ar', 'rnn', 'spectral'],  # Phase 3
                'index_advisor_version': '1.0',  # Phase 4
                'templatizer_version': '2.0'  # Enhanced templatizer
            }
        }

        logger.info(f"Analysis complete. Found {enhanced_summary['unique_templates']} unique templates, "
                   f"{clustering_result['num_clusters'] if clustering_result else 0} clusters, "
                   f"{len(recommendations)} recommendations")

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in analyze_workload: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# New Phase 2 endpoints
@app.route('/clusters', methods=['GET'])
def get_clusters():
    """Get current cluster information"""
    global workload_store

    try:
        if not workload_store:
            return jsonify({'error': 'Database not available'}), 503

        cluster_summary = workload_store.get_cluster_summary()
        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'clusters': cluster_summary,
            'num_clusters': len(cluster_summary)
        })

    except Exception as e:
        logger.error(f"Error in get_clusters: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/clusters/<int:cluster_id>', methods=['GET'])
def get_cluster_details(cluster_id):
    """Get detailed information about a specific cluster"""
    global workload_store

    try:
        if not workload_store:
            return jsonify({'error': 'Database not available'}), 503

        # Get cluster assignments
        assignments = workload_store.get_cluster_assignments(cluster_id)

        # Get cluster summary
        cluster_summary = workload_store.get_cluster_summary(cluster_id)
        cluster_info = cluster_summary[0] if cluster_summary else None

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'cluster_id': cluster_id,
            'cluster_info': cluster_info,
            'assignments': assignments,
            'template_count': len(assignments)
        })

    except Exception as e:
        logger.error(f"Error in get_cluster_details: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/templates/<template_hash>', methods=['GET'])
def get_template_details(template_hash):
    """Get detailed information about a specific template"""
    global workload_store

    try:
        if not workload_store:
            return jsonify({'error': 'Database not available'}), 503

        template_info = workload_store.get_template_info(template_hash)
        if not template_info:
            return jsonify({'error': 'Template not found'}), 404

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'template_info': template_info
        })

    except Exception as e:
        logger.error(f"Error in get_template_details: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/workload_timeseries', methods=['GET'])
def get_workload_timeseries():
    """Get workload time series data"""
    global workload_store

    try:
        if not workload_store:
            return jsonify({'error': 'Database not available'}), 503

        template_hash = request.args.get('template_hash')
        start_time = request.args.get('start_time')
        end_time = request.args.get('end_time')

        # Parse dates if provided
        if start_time:
            start_time = datetime.fromisoformat(start_time)
        if end_time:
            end_time = datetime.fromisoformat(end_time)

        time_series = workload_store.get_workload_timeseries(
            template_hash=template_hash,
            start_time=start_time,
            end_time=end_time
        )

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'time_series': time_series,
            'num_templates': len(time_series)
        })

    except Exception as e:
        logger.error(f"Error in get_workload_timeseries: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/ingest_query', methods=['POST'])
def ingest_query():
    """Ingest a single query"""
    global request_count
    request_count += 1

    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        sql = data.get('sql')
        if not sql:
            return jsonify({'error': 'Missing sql field'}), 400

        timestamp = data.get('timestamp')
        if not timestamp:
            timestamp = datetime.now().isoformat()

        # Simple template extraction (in real implementation, use full parser)
        template = sql.replace("'text'", "'&&&'") \
                     .replace("123", "#") \
                     .replace("456", "#")

        import hashlib
        template_hash = hashlib.md5(template.encode()).hexdigest()

        logger.info(f"Ingested query, template hash: {template_hash}")

        # Log to file (append mode)
        log_file = data.get('log_file', '/tmp/neurdb_workload.csv')
        with open(log_file, 'a') as f:
            f.write(f"{timestamp},{template_hash},\"{template}\",\"{sql}\"\n")

        return jsonify({
            'status': 'logged',
            'template_hash': template_hash,
            'template': template
        })

    except Exception as e:
        logger.error(f"Error in ingest_query: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# Phase 3 Forecasting Endpoints

@app.route('/forecast/models/train', methods=['POST'])
def train_forecasting_models():
    """Train forecasting models"""
    global forecasting_pipeline

    try:
        if forecasting_pipeline is None:
            return jsonify({
                'status': 'error',
                'message': 'Forecasting pipeline not initialized'
            }), 503

        data = request.get_json() or {}

        # Get training parameters
        model_types = data.get('model_types', ['ar', 'rnn', 'spectral'])
        train_ensemble = data.get('train_ensemble', True)
        template_hash = data.get('template_hash')
        cluster_id = data.get('cluster_id')
        days_back = data.get('days_back', 30)

        logger.info(f"Training forecasting models: {model_types}")

        # Train models
        if template_hash or cluster_id:
            # Train specific model
            model_key = template_hash if template_hash else f"cluster_{cluster_id}"
            training_data = forecasting_pipeline.prepare_training_data(
                template_hash=template_hash,
                cluster_id=cluster_id,
                days_back=days_back
            )

            if not training_data:
                return jsonify({
                    'status': 'error',
                    'message': 'No training data found'
                }), 404

            time_series = list(training_data.values())[0]
            stats = {'successful_trainings': {}, 'failed_trainings': {}}

            # Train individual models
            for model_type in model_types:
                forecaster = forecasting_pipeline.train_model(model_type, time_series, model_key)
                if forecaster:
                    stats['successful_trainings'][model_type] = 1
                else:
                    stats['failed_trainings'][model_type] = 1

            # Train ensemble
            if train_ensemble:
                ensemble = forecasting_pipeline.train_ensemble(time_series, model_key, model_types)
                stats['ensemble_trained'] = ensemble is not None

        else:
            # Train all models
            stats = forecasting_pipeline.train_all_models(model_types, train_ensemble)

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'training_stats': stats,
            'message': 'Model training completed'
        })

    except Exception as e:
        logger.error(f"Error in train_forecasting_models: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/forecast/generate', methods=['POST'])
def generate_forecasts():
    """Generate forecasts using trained models"""
    global forecasting_pipeline

    try:
        if forecasting_pipeline is None:
            return jsonify({
                'status': 'error',
                'message': 'Forecasting pipeline not initialized'
            }), 503

        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        # Get forecasting parameters
        model_key = data.get('model_key')  # template_hash or cluster_X
        horizon_minutes = data.get('horizon_minutes', 60)
        model_types = data.get('model_types', ['ar', 'rnn', 'spectral', 'ensemble'])

        if not model_key:
            return jsonify({'error': 'Missing model_key'}), 400

        logger.info(f"Generating forecasts for {model_key}")

        # Generate forecasts
        forecasts = forecasting_pipeline.generate_forecasts(
            model_key=model_key,
            horizon_minutes=horizon_minutes,
            model_types=model_types
        )

        if not forecasts:
            return jsonify({
                'status': 'error',
                'message': 'No forecasts generated - model not trained or insufficient data'
            }), 404

        # Prepare response
        response = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'model_key': model_key,
            'horizon_minutes': horizon_minutes,
            'forecasts': {}
        }

        for model_type, predictions in forecasts.items():
            response['forecasts'][model_type] = [
                {
                    'timestamp': ts.isoformat(),
                    'value': float(val)
                }
                for ts, val in predictions
            ]

        # Add ensemble prediction if available
        if 'ensemble' in forecasts:
            ensemble_values = [val for _, val in forecasts['ensemble']]
            response['summary'] = {
                'predicted_total_queries': sum(ensemble_values),
                'avg_queries_per_minute': np.mean(ensemble_values),
                'peak_queries_per_minute': max(ensemble_values),
                'peak_time': forecasts['ensemble'][np.argmax(ensemble_values)][0].isoformat()
            }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in generate_forecasts: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/forecast/models', methods=['GET'])
def list_forecasting_models():
    """List available forecasting models"""
    global forecasting_pipeline

    try:
        if forecasting_pipeline is None:
            return jsonify({
                'status': 'error',
                'message': 'Forecasting pipeline not initialized'
            }), 503

        metadata = forecasting_pipeline.get_model_metadata()
        summary = forecasting_pipeline.get_training_summary()

        # Format response
        models = []
        for model_id, info in metadata.items():
            models.append({
                'id': model_id,
                'type': info['model_type'],
                'key': info['model_key'],
                'trained_at': info['trained_at'],
                'training_samples': info['training_samples'],
                'model_info': info.get('model_info', {})
            })

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'summary': summary,
            'models': models
        })

    except Exception as e:
        logger.error(f"Error in list_forecasting_models: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/forecast/models/<model_key>/evaluate', methods=['POST'])
def evaluate_forecasting_model(model_key):
    """Evaluate forecasting model performance"""
    global forecasting_pipeline

    try:
        if forecasting_pipeline is None:
            return jsonify({
                'status': 'error',
                'message': 'Forecasting pipeline not initialized'
            }), 503

        data = request.get_json() or {}
        model_types = data.get('model_types', ['ar', 'rnn', 'spectral'])
        test_days = data.get('test_days', 7)

        logger.info(f"Evaluating models for {model_key}")

        # Get test data
        test_data_dict = forecasting_pipeline.prepare_training_data(
            template_hash=model_key if not model_key.startswith('cluster_') else None,
            cluster_id=int(model_key.split('_')[1]) if model_key.startswith('cluster_') else None,
            days_back=test_days
        )

        if not test_data_dict:
            return jsonify({
                'status': 'error',
                'message': 'No test data found'
            }), 404

        test_data = list(test_data_dict.values())[0]

        # Evaluate models
        results = forecasting_pipeline.evaluate_models(
            model_key=model_key,
            test_data=test_data,
            model_types=model_types
        )

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'model_key': model_key,
            'test_samples': len(test_data),
            'evaluation_results': results
        })

    except Exception as e:
        logger.error(f"Error in evaluate_forecasting_model: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/forecast/status', methods=['GET'])
def get_forecasting_status():
    """Get forecasting pipeline status"""
    global forecasting_pipeline

    try:
        if forecasting_pipeline is None:
            return jsonify({
                'status': 'not_initialized',
                'message': 'Forecasting pipeline not initialized'
            })

        summary = forecasting_pipeline.get_training_summary()
        return jsonify({
            'status': 'active',
            'timestamp': datetime.now().isoformat(),
            'summary': summary
        })

    except Exception as e:
        logger.error(f"Error in get_forecasting_status: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# Phase 4 Index Recommendation Endpoints

import asyncio

def run_async(coro):
    """Run async function in sync context"""
    if has_request_context():
        # If we're in a Flask request context, we need to run in the event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, we need to use create_task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except RuntimeError:
            # No event loop, create one
            return asyncio.run(coro)
    else:
        return asyncio.run(coro)

@app.route('/index/recommendations/generate', methods=['POST'])
def generate_index_recommendations():
    """Generate comprehensive index recommendations using forecasting and hypothetical analysis"""
    global forecast_index_service

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        # Initialize service if not already done
        if forecast_index_service is None:
            # Create configuration from request
            service_config = ForecastIndexConfig(
                database_host=data.get('database_host', 'localhost'),
                database_port=data.get('database_port', 5432),
                database_name=data.get('database_name', 'neurdb'),
                database_user=data.get('database_user', 'postgres'),
                database_password=data.get('database_password', 'postgres'),
                mode=ServiceMode(data.get('mode', 'on_demand')),
                schedule_interval_minutes=data.get('schedule_interval_minutes', 60),
                forecast_horizon_hours=data.get('forecast_horizon_hours', 24),
                forecasting_models=data.get('forecasting_models', ['AR', 'RNN', 'Spectral', 'Ensemble']),
                recommendation_strategy=RecommendationStrategy(
                    data.get('recommendation_strategy', 'balanced')
                ),
                optimization_approach=data.get('optimization_approach', 'pattern'),
                max_recommendations=data.get('max_recommendations', 50),
                enable_hypothetical_analysis=data.get('enable_hypothetical_analysis', True),
                require_forecast_validation=data.get('require_forecast_validation', False),
                lookback_hours=data.get('lookback_hours', 168),
                min_template_frequency=data.get('min_template_frequency', 5),
                max_templates_to_analyze=data.get('max_templates_to_analyze', 100),
                cost_based_time_limit_seconds=data.get('cost_based_time_limit_seconds', 60),
                cost_based_max_indexes_per_table=data.get('cost_based_max_indexes_per_table', 3),
                cost_based_progressive_optimization=data.get('cost_based_progressive_optimization', True)
            )

            forecast_index_service = ForecastIndexService(service_config)
            logger.info("Initialized ForecastIndexService")

        # Parse time parameters
        start_time = None
        end_time = None
        if data.get('start_time'):
            start_time = datetime.fromisoformat(data['start_time'])
        if data.get('end_time'):
            end_time = datetime.fromisoformat(data['end_time'])

        force_refresh = data.get('force_refresh', False)

        logger.info(f"Generating index recommendations with parameters: {data}")

        # Generate recommendations
        result = run_async(forecast_index_service.generate_recommendations(
            start_time=start_time,
            end_time=end_time,
            force_refresh=force_refresh
        ))

        # Convert result to JSON-serializable format
        response = {
            'status': 'success',
            'timestamp': result.timestamp.isoformat(),
            'analysis_period_hours': result.analysis_period_hours,
            'forecast_horizon_hours': result.forecast_horizon_hours,
            'total_templates_analyzed': result.total_templates_analyzed,
            'total_recommendations_generated': result.total_recommendations_generated,
            'high_confidence_recommendations': result.high_confidence_recommendations,
            'estimated_total_impact_queries': result.estimated_total_impact_queries,
            'estimated_cost_savings': result.estimated_cost_savings,
            'forecast_summary': result.forecast_summary,
            'workload_summary': result.workload_summary,
            'recommendations': []
        }

        # Serialize recommendations
        for rec in result.recommendations:
            rec_data = {
                'priority': rec.implementation_priority,
                'table': rec.candidate.table_name,
                'columns': rec.candidate.columns,
                'index_type': rec.candidate.index_type.value,
                'scenario': rec.candidate.scenario.value,
                'strength': rec.recommendation_strength,
                'combined_score': rec.combined_score,
                'confidence_score': rec.candidate.confidence_score,
                'estimated_benefit': rec.candidate.estimated_benefit,
                'estimated_size_mb': rec.candidate.estimated_size_mb,
                'creation_cost': rec.candidate.creation_cost,
                'maintenance_overhead': rec.candidate.maintenance_overhead,
                'impact_queries': rec.candidate.estimated_impact_queries,
                'risk_level': rec.risk_level,
                'estimated_roi': rec.estimated_roi,
                'sql_statements': rec.candidate.sql_statements,
                'reasoning': rec.reasoning,
                'warnings': rec.warnings
            }

            # Add hypothetical analysis if available
            if rec.hypothetical_analysis:
                rec_data['hypothetical_analysis'] = {
                    'cost_reduction_percentage': rec.hypothetical_analysis.cost_reduction_percentage,
                    'time_reduction_percentage': rec.hypothetical_analysis.time_reduction_percentage,
                    'plan_changes': rec.hypothetical_analysis.plan_changes,
                    'index_usage_detected': rec.hypothetical_analysis.index_usage_detected,
                    'analysis_status': rec.hypothetical_analysis.analysis_status.value
                }

            response['recommendations'].append(rec_data)

        logger.info(f"Generated {len(result.recommendations)} index recommendations")
        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in generate_index_recommendations: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/index/recommendations/latest', methods=['GET'])
def get_latest_recommendations():
    """Get the most recent index recommendations"""
    global forecast_index_service

    try:
        if forecast_index_service is None:
            return jsonify({
                'status': 'error',
                'message': 'Index service not initialized'
            }), 503

        # Get format parameter
        format_type = request.args.get('format', 'json')

        # Export latest results
        if format_type == 'json':
            result_json = forecast_index_service.export_latest_results('json')
            if result_json == "No results available":
                return jsonify({
                    'status': 'info',
                    'message': 'No recommendations available',
                    'recommendations': []
                })

            # Parse the JSON and return
            result_data = json.loads(result_json)
            return jsonify({
                'status': 'success',
                'timestamp': datetime.now().isoformat(),
                'recommendations_data': result_data
            })

        elif format_type == 'sql':
            sql_output = forecast_index_service.export_latest_results('sql')
            if sql_output == "No results available":
                return jsonify({
                    'status': 'info',
                    'message': 'No recommendations available'
                })

            return jsonify({
                'status': 'success',
                'format': 'sql',
                'timestamp': datetime.now().isoformat(),
                'sql_script': sql_output
            })

        elif format_type == 'markdown':
            markdown_output = forecast_index_service.export_latest_results('markdown')
            if markdown_output == "No results available":
                return jsonify({
                    'status': 'info',
                    'message': 'No recommendations available'
                })

            return jsonify({
                'status': 'success',
                'format': 'markdown',
                'timestamp': datetime.now().isoformat(),
                'markdown_report': markdown_output
            })

        else:
            return jsonify({
                'status': 'error',
                'message': f'Unsupported format: {format_type}. Supported formats: json, sql, markdown'
            }), 400

    except Exception as e:
        logger.error(f"Error in get_latest_recommendations: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/index/recommendations/export', methods=['GET'])
def export_recommendations():
    """Export index recommendations in specified format"""
    global forecast_index_service

    try:
        if forecast_index_service is None:
            return jsonify({
                'status': 'error',
                'message': 'Index service not initialized'
            }), 503

        # Get format parameter
        format_type = request.args.get('format', 'json')

        # Validate format
        if format_type not in ['json', 'sql', 'markdown']:
            return jsonify({
                'status': 'error',
                'message': f'Unsupported format: {format_type}. Supported formats: json, sql, markdown'
            }), 400

        # Export recommendations
        export_data = forecast_index_service.export_latest_results(format_type)

        if export_data == "No results available":
            return jsonify({
                'status': 'info',
                'message': 'No recommendations available to export'
            })

        # Set appropriate content type for file download
        if format_type == 'json':
            return export_data, 200, {'Content-Type': 'application/json'}
        elif format_type == 'sql':
            return export_data, 200, {'Content-Type': 'application/sql'}
        elif format_type == 'markdown':
            return export_data, 200, {'Content-Type': 'text/markdown'}

        return jsonify({
            'status': 'success',
            'format': format_type,
            'timestamp': datetime.now().isoformat(),
            'data': export_data
        })

    except Exception as e:
        logger.error(f"Error in export_recommendations: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/index/recommendations/history', methods=['GET'])
def get_recommendation_history():
    """Get history of index recommendation analyses"""
    global forecast_index_service

    try:
        if forecast_index_service is None:
            return jsonify({
                'status': 'error',
                'message': 'Index service not initialized'
            }), 503

        # Get limit parameter
        limit = request.args.get('limit', 10, type=int)
        if limit < 1:
            limit = 10

        # Get history
        history = run_async(forecast_index_service.get_recommendation_history(limit))

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'history': history,
            'count': len(history)
        })

    except Exception as e:
        logger.error(f"Error in get_recommendation_history: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/index/service/start', methods=['POST'])
def start_index_service():
    """Start the index recommendation service"""
    global forecast_index_service

    try:
        data = request.get_json() or {}

        # Create configuration if service doesn't exist
        if forecast_index_service is None:
            service_config = ForecastIndexConfig(
                database_host=data.get('database_host', 'localhost'),
                database_port=data.get('database_port', 5432),
                database_name=data.get('database_name', 'neurdb'),
                database_user=data.get('database_user', 'postgres'),
                database_password=data.get('database_password', 'postgres'),
                mode=ServiceMode(data.get('mode', 'on_demand')),
                schedule_interval_minutes=data.get('schedule_interval_minutes', 60),
                forecast_horizon_hours=data.get('forecast_horizon_hours', 24),
                recommendation_strategy=RecommendationStrategy(
                    data.get('recommendation_strategy', 'balanced')
                ),
                max_recommendations=data.get('max_recommendations', 50),
                enable_hypothetical_analysis=data.get('enable_hypothetical_analysis', True)
            )

            forecast_index_service = ForecastIndexService(service_config)

        # Start the service
        run_async(forecast_index_service.start())

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'message': 'Index service started successfully'
        })

    except Exception as e:
        logger.error(f"Error in start_index_service: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/index/service/status', methods=['GET'])
def get_index_service_status():
    """Get index recommendation service status"""
    global forecast_index_service

    try:
        if forecast_index_service is None:
            return jsonify({
                'status': 'not_initialized',
                'message': 'Index service not initialized'
            })

        status = run_async(forecast_index_service.get_service_status())

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'service_status': status
        })

    except Exception as e:
        logger.error(f"Error in get_index_service_status: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/index/service/stop', methods=['POST'])
def stop_index_service():
    """Stop the index recommendation service"""
    global forecast_index_service

    try:
        if forecast_index_service is None:
            return jsonify({
                'status': 'info',
                'message': 'Index service not running'
            })

        run_async(forecast_index_service.stop())

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'message': 'Index service stopped successfully'
        })

    except Exception as e:
        logger.error(f"Error in stop_index_service: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


# Cost-Based Optimization Endpoints

@app.route('/index/recommendations/cost_based', methods=['POST'])
def generate_cost_based_recommendations():
    """Generate cost-based index recommendations using hypopg and DTA-inspired optimization"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        # Import cost-based advisor
        from cost_based_index_advisor import CostBasedIndexAdvisor, IndexType

        # Database connection parameters
        db_params = {
            'host': data.get('database_host', 'localhost'),
            'port': data.get('database_port', 15432),
            'database': data.get('database_name', 'neurdb'),
            'user': data.get('database_user', 'neurdb'),
            'password': data.get('database_password', 'postgres')
        }

        # Initialize cost-based advisor
        advisor = CostBasedIndexAdvisor(db_params)

        # Get workload queries from request
        workload_queries = data.get('workload_queries', [])
        if not workload_queries:
            return jsonify({'error': 'No workload queries provided'}), 400

        # Get schema information if provided
        schema_info = data.get('schema_info', {})
        if not schema_info:
            # Use default schema for testing
            schema_info = {
                'movie_info': {'columns': ['movie_id', 'info_type_id', 'info'], 'size_mb': 500},
                'cast_info': {'columns': ['person_id', 'movie_id', 'role_id', 'note'], 'size_mb': 800},
                'title': {'columns': ['title_id', 'title', 'kind_id', 'production_year'], 'size_mb': 600},
                'person_name': {'columns': ['person_id', 'name', 'gender'], 'size_mb': 100},
                'movie_keyword': {'columns': ['movie_id', 'keyword_id'], 'size_mb': 300}
            }

        # Optimization parameters
        time_limit_seconds = data.get('time_limit_seconds', 60)
        max_indexes_per_table = data.get('max_indexes_per_table', 3)

        logger.info(f"Starting cost-based optimization with {len(workload_queries)} queries")

        # Run cost-based optimization
        result = advisor.recommend_indexes(
            workload_queries=workload_queries,
            schema_info=schema_info,
            time_limit_seconds=time_limit_seconds
        )

        # Prepare response
        response = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'optimization_approach': 'cost_based',
            'optimization_time_seconds': result.optimization_time,
            'final_benefit_score': result.best_solution.benefit_score,
            'quality_improvements': [
                {'time_seconds': time_improvement, 'benefit_score': benefit_score}
                for time_improvement, benefit_score in result.quality_improvements
            ],
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
                'creation_cost': index.creation_cost,
                'benefit_score': index.benefit_score,
                'sql_statement': f"CREATE INDEX idx_{index.table_name}_{'_'.join(index.columns)} ON {index.table_name} ({', '.join(index.columns)}) USING {index.index_type.value}"
            }
            response['recommended_indexes'].append(index_data)

        # Add solution summary
        response['solution_summary'] = {
            'total_indexes': len(result.best_solution.indexes),
            'storage_cost_mb': result.best_solution.storage_cost,
            'maintenance_cost': result.best_solution.maintenance_cost,
            'net_benefit_score': result.best_solution.benefit_score
        }

        logger.info(f"Cost-based optimization complete: {len(result.best_solution.indexes)} indexes recommended")
        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in generate_cost_based_recommendations: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/index/recommendations/cost_based/compare', methods=['GET'])
def compare_optimization_approaches():
    """Compare pattern-based vs cost-based optimization approaches"""
    try:
        # Get sample workload queries for comparison
        sample_queries = [
            "SELECT * FROM movie_info WHERE movie_id = 12345",
            "SELECT * FROM cast_info WHERE person_id = 67890 AND role_id = 1",
            "SELECT * FROM title WHERE production_year > 2000 ORDER BY title",
            "SELECT COUNT(*) FROM movie_keyword WHERE keyword_id = 11111",
            "SELECT name FROM person_name WHERE name LIKE 'John%' AND gender = 'm'",
            "SELECT * FROM movie_info WHERE info_type_id = 100 AND movie_id IN (1,2,3,4,5)",
            "SELECT DISTINCT movie_id FROM cast_info WHERE person_id = 12345 ORDER BY movie_id",
            "SELECT * FROM title WHERE kind_id = 1 AND production_year BETWEEN 1990 AND 2020",
            "SELECT COUNT(*) FROM cast_info GROUP BY role_id",
            "SELECT * FROM movie_info WHERE movie_id = 12345 AND info_type_id = 3"
        ]

        # Pattern-based approach description
        pattern_based_info = {
            'approach': 'pattern_based',
            'methodology': 'Regex pattern recognition and heuristic scoring',
            'strengths': [
                'Fast execution',
                'Low computational overhead',
                'Good for obvious index patterns',
                'Works with minimal statistics'
            ],
            'limitations': [
                'Limited to predefined patterns',
                'No real PostgreSQL cost validation',
                'May miss complex optimization opportunities',
                'Heuristic-based scoring may be suboptimal'
            ],
            'typical_performance': {
                'execution_time_ms': '100-1000',
                'memory_usage_mb': '10-50',
                'accuracy': '60-80%'
            }
        }

        # Cost-based approach description
        cost_based_info = {
            'approach': 'cost_based',
            'methodology': 'DTA-inspired progressive optimization with hypopg',
            'strengths': [
                'Uses PostgreSQL\'s actual cost model',
                'Validates with real query planner',
                'Combinatorial search space exploration',
                'Progressive quality improvement',
                'Optimal solution guarantees'
            ],
            'limitations': [
                'Higher computational cost',
                'Requires accurate statistics',
                'Longer optimization time',
                'Complex implementation'
            ],
            'typical_performance': {
                'execution_time_ms': '5000-60000',
                'memory_usage_mb': '100-500',
                'accuracy': '85-95%'
            }
        }

        # Comparison table
        comparison = {
            'aspect': [
                'Cost Estimation',
                'Search Strategy',
                'Validation Method',
                'Optimization Quality',
                'Execution Speed',
                'Resource Requirements',
                'Scalability',
                'Best Use Case'
            ],
            'pattern_based': [
                'Heuristic scoring based on frequency',
                'Single-pass pattern recognition',
                'None (no validation)',
                'Fast approximation',
                'Very Fast',
                'Low',
                'Good for large workloads',
                'Quick recommendations, obvious patterns'
            ],
            'cost_based': [
                'PostgreSQL EXPLAIN ANALYZE with hypopg',
                'DTA-inspired progressive search',
                'Real PostgreSQL query planner validation',
                'Near-optimal with guarantees',
                'Slower but progressive',
                'Medium to High',
                'Best for moderate workloads',
                'Critical performance optimization'
            ]
        }

        # Recommendations for when to use each approach
        usage_recommendations = {
            'use_pattern_based_when': [
                'Workload has >1000 queries',
                'Need quick recommendations',
                'Limited computational resources',
                'Obvious indexing patterns exist',
                'Real-time recommendation required'
            ],
            'use_cost_based_when': [
                'Workload has <500 queries',
                'Performance is critical',
                'Sufficient time for optimization',
                'Complex query patterns',
                'Maximum optimization quality needed'
            ],
            'hybrid_approach': [
                'Use pattern-based for initial filtering',
                'Apply cost-based to top candidates',
                'Combine speed with accuracy',
                'Progressive refinement strategy'
            ]
        }

        return jsonify({
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'pattern_based': pattern_based_info,
            'cost_based': cost_based_info,
            'comparison': comparison,
            'usage_recommendations': usage_recommendations,
            'sample_workload_size': len(sample_queries),
            'note': 'Actual performance varies based on database size, statistics accuracy, and query complexity'
        })

    except Exception as e:
        logger.error(f"Error in compare_optimization_approaches: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


def find_indexes_to_evict(cursor, required_space_mb, current_budget, usage_threshold=10):
    """Find indexes to evict when storage budget is exceeded

    Returns list of indexes to evict based on:
    - Low usage (below threshold)
    - Small size relative to required space
    - High benefit score from queries
    """
    try:
        # Query for low-usage indexes that we can potentially remove
        cursor.execute("""
            SELECT
                schemaname,
                indexrelname,
                pg_relation_size(indexrelid) / 1024.0 / 1024.0 as size_mb,
                idx_scan,
                idx_tup_read,
                idx_tup_fetch
            FROM pg_stat_user_indexes ui
            JOIN pg_index i ON ui.indexrelid = i.indexrelid
            WHERE
                -- Only consider auto-created indexes (idx_auto_ prefix)
                indexrelname LIKE 'idx_auto_%'
                -- Low benefit = low usage relative to size
                AND (
                    -- Has very low usage (below threshold)
                    idx_scan < %s
                    OR
                    -- Or has poor benefit ratio (high size, low usage)
                    -- Formula: large indexes with very few scans are good eviction candidates
                    (pg_relation_size(indexrelid) > 100 * 1024 * 1024 AND idx_scan < 100)
                )
                -- Not a primary key
                AND NOT i.indisprimary
                -- Not used for constraint enforcement
                AND NOT i.indisunique
            ORDER BY
                -- Order by eviction priority:
                -- 1. Largest size per scan (worst benefit first)
                -- 2. Zero scan indexes first (unused)
                (pg_relation_size(indexrelid) / 1024.0 / 1024.0) / NULLIF(idx_scan, 0) DESC NULLS FIRST,
                idx_scan ASC NULLS FIRST,
                pg_relation_size(indexrelid) DESC
        """, (usage_threshold,))

        candidates = cursor.fetchall()

        indexes_to_evict = []
        freed_space = 0.0

        for candidate in candidates:
            # Handle both tuple and dict access
            if isinstance(candidate, dict):
                size_mb = candidate.get('size_mb', 0)
                index_name = candidate.get('indexrelname', '')
            else:
                size_mb = float(candidate[2]) if candidate[2] is not None else 0
                index_name = str(candidate[1]) if candidate[1] is not None else ''

            indexes_to_evict.append({
                'index_name': index_name,
                'size_mb': size_mb
            })
            freed_space += size_mb

            # Stop if we've freed enough space for the new index
            if freed_space >= required_space_mb:
                break

        return indexes_to_evict, freed_space

    except Exception as e:
        logger.error(f"Error finding indexes to evict: {e}")
        return [], 0.0


def evict_index(cursor, index_name):
    """Evict (drop) an index and return its size"""
    try:
        # Get size before dropping
        cursor.execute("""
            SELECT pg_relation_size(%s::regclass) / 1024.0 / 1024.0
        """, (index_name,))
        size_result = cursor.fetchone()

        # Handle both tuple and dict access
        if isinstance(size_result, dict):
            size_mb = size_result.get('pg_relation_size', 0)
        else:
            size_mb = float(size_result[0]) if size_result else 0

        # Drop the index
        cursor.execute(f"DROP INDEX IF EXISTS {index_name}")

        return size_mb, True

    except Exception as e:
        logger.error(f"Error evicting index {index_name}: {e}")
        return 0.0, False


@app.route('/index/auto_create', methods=['POST'])
def auto_create_indexes():
    """Automatically create indexes based on recommendations and storage budget"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        # Database connection parameters
        db_params = {
            'host': data.get('database_host', 'localhost'),
            'port': data.get('database_port', 5432),
            'database': data.get('database_name', 'neurdb'),
            'user': data.get('database_user', 'neurdb'),
            'password': data.get('database_password', 'postgres')
        }

        # Get recommended indexes (either from request or generate them)
        recommended_indexes = data.get('recommended_indexes', [])

        if not recommended_indexes:
            # Generate recommendations first if not provided
            workload_queries = data.get('workload_queries', [])
            if not workload_queries:
                return jsonify({'error': 'No workload queries or recommendations provided'}), 400

            # Generate cost-based recommendations
            schema_info = data.get('schema_info', {})

            from cost_based_index_advisor import CostBasedIndexAdvisor
            advisor = CostBasedIndexAdvisor(db_params)

            result = advisor.recommend_indexes(
                workload_queries=workload_queries,
                schema_info=schema_info,
                time_limit_seconds=data.get('time_limit_seconds', 30)
            )

            # Convert recommended indexes to the expected format
            for index in result.best_solution.indexes:
                recommended_indexes.append({
                    'table_name': index.table_name,
                    'columns': index.columns,
                    'index_type': index.index_type.value,
                    'benefit_score': index.benefit_score,
                    'estimated_size_mb': index.estimated_size_mb,
                    'sql_statement': f"CREATE INDEX idx_{index.table_name}_{'_'.join(index.columns)} ON {index.table_name} ({', '.join(index.columns)})"
                })

        # Sort by benefit score (highest first)
        recommended_indexes.sort(key=lambda x: x.get('benefit_score', 0), reverse=True)

        # Connect to database and auto-create indexes
        import psycopg2
        from psycopg2.extras import RealDictCursor

        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        created_indexes = []
        failed_indexes = []
        evicted_indexes = []
        total_size_added = 0.0

        logger.info(f"Starting auto-index creation with {len(recommended_indexes)} recommendations")

        # Check current storage usage and budget
        try:
            cursor.execute("SELECT nr_calculate_index_budget_mb() as budget, nr_get_current_index_storage_mb() as current_usage")
            budget_info = cursor.fetchone()

            if budget_info is None:
                logger.error("Failed to get budget info: query returned no results")
                return jsonify({'error': 'Failed to get storage budget information'}), 500

            # Handle both dictionary access and tuple access based on cursor type
            if isinstance(budget_info, dict):
                current_budget = budget_info.get('budget', 0.0)
                current_usage = budget_info.get('current_usage', 0.0)
            else:
                # Tuple access - order matches SELECT
                current_budget = float(budget_info[0]) if budget_info[0] is not None else 0.0
                current_usage = float(budget_info[1]) if budget_info[1] is not None else 0.0

        except Exception as e:
            logger.error(f"Error getting budget info: {e}")
            return jsonify({'error': f'Failed to get storage budget: {str(e)}'}), 500

        available_budget = current_budget - current_usage

        logger.info(f"Index storage budget: {current_budget:.2f} MB, Current usage: {current_usage:.2f} MB, Available: {available_budget:.2f} MB")

        for idx, index_rec in enumerate(recommended_indexes):
            table_name = index_rec.get('table_name')
            columns = index_rec.get('columns', [])
            index_type = index_rec.get('index_type', 'btree')
            estimated_size = index_rec.get('estimated_size_mb', 10.0)
            benefit_score = index_rec.get('benefit_score', 0.0)

            # Check if we have enough budget
            if estimated_size > available_budget:
                logger.info(f"Estimated size {estimated_size:.2f} MB exceeds available budget {available_budget:.2f} MB")

                # Try to evict low-usage indexes to make space
                required_space = estimated_size - available_budget + 50.0  # Need extra buffer
                indexes_to_evict, freed_space = find_indexes_to_evict(cursor, required_space, current_budget)

                if freed_space >= required_space:
                    # Evict indexes
                    evicted_list = []
                    for eviction in indexes_to_evict:
                        freed, success = evict_index(cursor, eviction['index_name'])
                        if success:
                            evicted_list.append({
                                'index_name': eviction['index_name'],
                                'size_mb': freed
                            })
                            available_budget += freed
                            logger.info(f"Evicted index {eviction['index_name']} ({freed:.2f} MB) for better utilization")

                    total_size_added -= sum(e['size_mb'] for e in evicted_list)

                    # Add eviction info to response
                    evicted_indexes.extend(evicted_list)

                else:
                    # Still not enough budget even after eviction
                    logger.warning(f"Cannot create index for {table_name} - insufficient budget even after eviction")
                    failed_indexes.append({
                        'table_name': table_name,
                        'columns': columns,
                        'reason': f'Insufficient budget after eviction (needs {estimated_size:.2f} MB, have {available_budget:.2f} MB, freed {freed_space:.2f} MB from {len(indexes_to_evict)} indexes)'
                    })
                    continue

            # Generate unique index name
            index_name = f"idx_auto_{table_name}_{'_'.join(columns[:3])}_{idx}"  # Limit column count in name
            index_name = index_name.replace('.', '_').replace('-', '_')[:63]  # PostgreSQL limit

            try:
                # Use the NeurDB index management function
                cursor.execute(
                    "SELECT nr_create_index_if_budget_allows(%s, %s, %s, %s)",
                    (index_name, table_name, columns, index_type)
                )

                result = cursor.fetchone()
                # Handle both dictionary and tuple access
                if isinstance(result, dict):
                    created = result.get('nr_create_index_if_budget_allows', False)
                else:
                    created = bool(result[0]) if result else False

                if created:
                    # Get actual size of created index
                    cursor.execute("SELECT pg_relation_size(%s::regclass) / 1024.0 / 1024.0 as size", (index_name,))
                    size_result = cursor.fetchone()
                    # Handle both dictionary and tuple access
                    if isinstance(size_result, dict):
                        actual_size = size_result.get('size', estimated_size)
                    else:
                        actual_size = float(size_result[0]) if size_result else estimated_size

                    created_indexes.append({
                        'index_name': index_name,
                        'table_name': table_name,
                        'columns': columns,
                        'index_type': index_type,
                        'estimated_size_mb': estimated_size,
                        'actual_size_mb': actual_size,
                        'benefit_score': benefit_score,
                        'sql_statement': f"CREATE INDEX {index_name} ON {table_name} ({', '.join(columns)}) USING {index_type}"
                    })

                    total_size_added += actual_size
                    available_budget -= actual_size
                    current_usage += actual_size

                    logger.info(f"Created index {index_name} ({actual_size:.2f} MB) - Remaining budget: {available_budget:.2f} MB")
                else:
                    failed_indexes.append({
                        'table_name': table_name,
                        'columns': columns,
                        'reason': 'Budget check failed or index creation failed'
                    })

            except Exception as e:
                logger.warning(f"Failed to create index for {table_name}: {e}")
                failed_indexes.append({
                    'table_name': table_name,
                    'columns': columns,
                    'reason': str(e)
                })

        conn.commit()
        cursor.close()
        conn.close()

        # Get final storage statistics
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()

        cursor.execute("SELECT nr_get_current_index_storage_mb() as final_usage, nr_calculate_index_budget_mb() as final_budget")
        final_stats = cursor.fetchone()

        cursor.close()
        conn.close()

        # Handle both dictionary and tuple access patterns
        if isinstance(final_stats, dict):
            final_budget = final_stats.get('final_budget', 0.0)
            final_usage_mb = final_stats.get('final_usage', 0.0)
        else:
            # Tuple access
            final_usage_mb = float(final_stats[0]) if final_stats and final_stats[0] is not None else 0.0
            final_budget = float(final_stats[1]) if final_stats and final_stats[1] is not None else 0.0

        response = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'storage_budget': {
                'budget_mb': final_budget,
                'usage_before_mb': current_usage,
                'usage_after_mb': final_usage_mb,
                'size_added_mb': total_size_added,
                'remaining_budget_mb': final_budget - final_usage_mb
            },
            'index_creation_results': {
                'total_recommendations': len(recommended_indexes),
                'created_indexes': len(created_indexes),
                'failed_indexes': len(failed_indexes),
                'success_rate': len(created_indexes) / len(recommended_indexes) if recommended_indexes else 0.0
            },
            'created_indexes': created_indexes,
            'evicted_indexes': evicted_indexes,
            'failed_indexes': failed_indexes,
            'message': f'Successfully created {len(created_indexes)} out of {len(recommended_indexes)} recommended indexes. Difference: {total_size_added:.2f} MB'
        }

        # Log summary including evictions
        if evicted_indexes:
            logger.info(f"Evicted {len(evicted_indexes)} indexes to make room: {[e['index_name'] for e in evicted_indexes]}")

        logger.info(f"Auto-index creation complete: {len(created_indexes)}/{len(recommended_indexes)} indexes created, {len(evicted_indexes)} evicted, {total_size_added:.2f} MB net change")
        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in auto_create_indexes: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/index/storage_stats', methods=['GET'])
def get_storage_stats():
    """Get current index storage statistics and budget information"""
    try:
        # Get database connection parameters from query string or use defaults
        db_params = {
            'host': request.args.get('host', 'localhost'),
            'port': request.args.get('port', 5432, type=int),
            'database': request.args.get('database', 'neurdb'),
            'user': request.args.get('user', 'postgres'),
            'password': request.args.get('password', '')
        }

        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get storage statistics
        cursor.execute("""
            SELECT
                nr_get_current_index_storage_mb() as current_usage_mb,
                nr_calculate_index_budget_mb() as budget_mb,
                nr_get_database_size_mb() as database_size_mb,
                (SELECT COUNT(*) FROM pg_class WHERE relkind = 'i' AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')) as total_indexes
        """)

        stats = cursor.fetchone()

        # Get per-index details
        cursor.execute("SELECT * FROM nr_index_storage_usage ORDER BY size_mb DESC")
        index_details = cursor.fetchall()

        cursor.close()
        conn.close()

        response = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'storage_summary': {
                'current_usage_mb': stats['current_usage_mb'] if stats else 0.0,
                'budget_mb': stats['budget_mb'] if stats else 0.0,
                'database_size_mb': stats['database_size_mb'] if stats else 0.0,
                'total_indexes': stats['total_indexes'] if stats else 0,
                'remaining_budget_mb': (stats['budget_mb'] - stats['current_usage_mb']) if stats else 0.0,
                'budget_utilization_percent': (stats['current_usage_mb'] / stats['budget_mb'] * 100) if stats and stats['budget_mb'] > 0 else 0.0
            },
            'index_details': [dict(row) for row in index_details]
        }

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error in get_storage_stats: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/guc_change', methods=['POST'])
def handle_guc_change():
    """Handle GUC parameter change notifications from PostgreSQL"""
    global request_count
    request_count += 1

    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'Invalid JSON'}), 400

        parameter = data.get('parameter')
        value = data.get('value')
        action = data.get('action')

        if parameter == 'nr_enable_auto_index_creation':
            logger.info(f"Received GUC change notification: {parameter} = {value} (action: {action})")

            if value is True and action == 'enable':
                # Start automatic index creation process
                logger.info("Auto-index creation is now enabled - AI engine will start monitoring workload")
                return jsonify({
                    'status': 'success',
                    'message': 'Auto-index creation enabled successfully',
                    'timestamp': datetime.now().isoformat(),
                    'action_taken': 'enabled'
                })
            elif value is False and action == 'disable':
                # Stop automatic index creation process
                logger.info("Auto-index creation is now disabled - AI engine will stop automatic index creation")
                return jsonify({
                    'status': 'success',
                    'message': 'Auto-index creation disabled successfully',
                    'timestamp': datetime.now().isoformat(),
                    'action_taken': 'disabled'
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': f'Invalid value or action for {parameter}: value={value}, action={action}'
                }), 400
        else:
            logger.warning(f"Received unknown GUC parameter change: {parameter}")
            return jsonify({
                'status': 'info',
                'message': f'GUC parameter {parameter} not handled by AI engine',
                'timestamp': datetime.now().isoformat()
            })

    except Exception as e:
        logger.error(f"Error in handle_guc_change: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


def create_sample_workload():
    """Create sample workload data for testing"""
    sample_queries = [
        "SELECT * FROM movie_info WHERE movie_id = 123",
        "SELECT * FROM person_info WHERE person_id = 456",
        "SELECT title, production_year FROM title WHERE kind_id = 'movie' AND production_year > 2000",
        "SELECT name, gender FROM name WHERE name LIKE 'John%'",
        "SELECT movie_id, info FROM movie_info WHERE info_type_id = 123 AND movie_id IN (1,2,3)",
        "SELECT * FROM cast_info WHERE movie_id = 789 AND role_id = 1",
        "SELECT * FROM movie_keyword WHERE keyword_id = 42 ORDER BY movie_id LIMIT 10"
    ]

    log_file = '/tmp/neurdb_workload.csv'

    # Create directory if needed
    os.makedirs(os.path.dirname(log_file), exist_ok=True)

    with open(log_file, 'w') as f:
        f.write("timestamp,template_hash,template,original_query\n")
        for i, query in enumerate(sample_queries):
            timestamp = datetime.now().isoformat()
            # Simple template extraction
            template = query.replace("'movie'", "'&&&'") \
                          .replace("2000", "#") \
                          .replace("123", "#") \
                          .replace("456", "#") \
                          .replace("789", "#") \
                          .replace("42", "#") \
                          .replace("John", "&&&") \
                          .replace("1,2,3", "#,#,#")
            import hashlib
            template_hash = hashlib.md5(template.encode()).hexdigest()

            # Escape quotes
            template_escaped = template.replace('"', '""')
            query_escaped = query.replace('"', '""')

            f.write(f"{timestamp},{template_hash},\"{template_escaped}\",\"{query_escaped}\"\n")

    return log_file


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='NeurDB Workload Forecast AI Engine Server'
    )
    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='Host to bind to (default: 0.0.0.0)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=8777,
        help='Port to bind to (default: 8777)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Run in debug mode'
    )
    parser.add_argument(
        '--create-sample',
        action='store_true',
        help='Create sample workload data for testing'
    )
    parser.add_argument(
        '--log-file',
        type=str,
        default='/tmp/neurdb_workload.csv',
        help='Path to workload log file'
    )

    args = parser.parse_args()

    # Create sample data if requested
    if args.create_sample:
        sample_file = create_sample_workload()
        print(f"✓ Created sample workload data at: {sample_file}")
        print(f"  Contains 7 sample queries for testing")

    logger.info(f"Starting NeurDB Workload Forecast AI Engine on {args.host}:{args.port}")
    print(f"=" * 60)
    print(f"NeurDB Workload Forecast AI Engine")
    print(f"Server: http://{args.host}:{args.port}")
    print(f"Log file: {args.log_file}")
    print(f"Debug mode: {args.debug}")
    print(f"=" * 60)
    print()
    print("Available endpoints:")
    print(f"  GET  http://{args.host}:{args.port}/")
    print(f"  GET  http://{args.host}:{args.port}/health")
    print(f"  POST http://{args.host}:{args.port}/analyze_workload")
    print(f"  POST http://{args.host}:{args.port}/ingest_query")
    print()
    print("Phase 3 Forecasting endpoints:")
    print(f"  POST http://{args.host}:{args.port}/forecast/models/train")
    print(f"  POST http://{args.host}:{args.port}/forecast/generate")
    print(f"  GET  http://{args.host}:{args.port}/forecast/models")
    print(f"  POST http://{args.host}:{args.port}/forecast/models/<model_key>/evaluate")
    print(f"  GET  http://{args.host}:{args.port}/forecast/status")
    print()
    print("Phase 4 Index Recommendation endpoints:")
    print(f"  POST http://{args.host}:{args.port}/index/recommendations/generate")
    print(f"  GET  http://{args.host}:{args.port}/index/recommendations/latest?format=json")
    print(f"  GET  http://{args.host}:{args.port}/index/recommendations/export?format=sql")
    print(f"  GET  http://{args.host}:{args.port}/index/recommendations/history")
    print(f"  POST http://{args.host}:{args.port}/index/service/start")
    print(f"  GET  http://{args.host}:{args.port}/index/service/status")
    print(f"  POST http://{args.host}:{args.port}/index/service/stop")
    print()
    print("Cost-Based Optimization endpoints:")
    print(f"  POST http://{args.host}:{args.port}/index/recommendations/cost_based")
    print(f"  GET  http://{args.host}:{args.port}/index/recommendations/cost_based/compare")
    print()
    print("Automatic Index Creation endpoints:")
    print(f"  POST http://{args.host}:{args.port}/index/auto_create")
    print(f"  GET  http://{args.host}:{args.port}/index/storage_stats")
    print()
    print("GUC Parameter Notification:")
    print(f"  POST http://{args.host}:{args.port}/guc_change")
    print()

    app.config['LOG_FILE'] = args.log_file

    try:
        app.run(
            host=args.host,
            port=args.port,
            debug=args.debug
        )
    except KeyboardInterrupt:
        print("\nServer stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}", exc_info=True)
        print(f"Server error: {e}")
