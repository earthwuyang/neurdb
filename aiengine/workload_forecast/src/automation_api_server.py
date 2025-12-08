#!/usr/bin/env python3
"""
Automation API Server for NeurDB Workload Forecast
REST API for managing automated index recommendations
"""

import json
import logging
from datetime import datetime
from typing import Dict, Optional
from flask import Flask, request, jsonify
from pathlib import Path

from automation_scheduler import (
    AutomationScheduler, AutomationConfig, AutomationStatus,
    RecommendationAction, AutomationMetrics
)

logger = logging.getLogger(__name__)


class AutomationAPIServer:
    """REST API server for automation management"""

    def __init__(self, scheduler: AutomationScheduler):
        """
        Initialize the API server

        Args:
            scheduler: Automation scheduler instance
        """
        self.scheduler = scheduler
        self.app = Flask(__name__)
        self.setup_routes()

    def setup_routes(self):
        """Setup all API routes"""

        @self.app.route('/automation/status', methods=['GET'])
        def get_automation_status():
            """Get current automation status and metrics"""
            try:
                status_data = self.scheduler.get_status()
                return jsonify({
                    'status': 'success',
                    'timestamp': datetime.now().isoformat(),
                    'automation': status_data
                })
            except Exception as e:
                logger.error(f"Error getting automation status: {e}")
                return jsonify({
                    'status': 'error',
                    'message': str(e)
                }), 500

        @self.app.route('/automation/start', methods=['POST'])
        def start_automation():
            """Start the automation scheduler"""
            try:
                self.scheduler.start()
                return jsonify({
                    'status': 'success',
                    'timestamp': datetime.now().isoformat(),
                    'message': 'Automation scheduler started'
                })
            except Exception as e:
                logger.error(f"Error starting automation: {e}")
                return jsonify({
                    'status': 'error',
                    'message': str(e)
                }), 500

        @self.app.route('/automation/stop', methods=['POST'])
        def stop_automation():
            """Stop the automation scheduler"""
            try:
                self.scheduler.stop()
                return jsonify({
                    'status': 'success',
                    'timestamp': datetime.now().isoformat(),
                    'message': 'Automation scheduler stopped'
                })
            except Exception as e:
                logger.error(f"Error stopping automation: {e}")
                return jsonify({
                    'status': 'error',
                    'message': str(e)
                }), 500

        @self.app.route('/automation/trigger', methods=['POST'])
        def trigger_analysis():
            """Manually trigger an analysis cycle"""
            try:
                import asyncio
                # Trigger analysis in background
                asyncio.create_task(self.scheduler._run_periodic_analysis_async())
                return jsonify({
                    'status': 'success',
                    'timestamp': datetime.now().isoformat(),
                    'message': 'Analysis cycle triggered'
                })
            except Exception as e:
                logger.error(f"Error triggering analysis: {e}")
                return jsonify({
                    'status': 'error',
                    'message': str(e)
                }), 500

        @self.app.route('/automation/health', methods=['GET'])
        def health_check():
            """Health check endpoint"""
            try:
                health_status = {
                    'healthy': self.scheduler.status in [AutomationStatus.RUNNING, AutomationStatus.PAUSED],
                    'status': self.scheduler.status.value,
                    'uptime_hours': self.scheduler.metrics.uptime_hours,
                    'last_analysis': self.scheduler.metrics.last_analysis_time.isoformat() if self.scheduler.metrics.last_analysis_time else None,
                    'total_errors': self.scheduler.metrics.total_errors,
                    'last_error': self.scheduler.metrics.last_error
                }

                return jsonify({
                    'status': 'success',
                    'timestamp': datetime.now().isoformat(),
                    'health': health_status
                })

            except Exception as e:
                logger.error(f"Error in health check: {e}")
                return jsonify({
                    'status': 'error',
                    'message': str(e)
                }), 500

        @self.app.route('/', methods=['GET'])
        def index():
            """API information and available endpoints"""
            return jsonify({
                'service': 'NeurDB Automation API Server',
                'version': '1.0.0',
                'description': 'REST API for automated workload analysis and index recommendations',
                'endpoints': {
                    'GET /': 'This API information',
                    'GET /automation/health': 'Health check',
                    'GET /automation/status': 'Get automation status and metrics',
                    'POST /automation/start': 'Start automation scheduler',
                    'POST /automation/stop': 'Stop automation scheduler',
                    'POST /automation/trigger': 'Manually trigger analysis'
                },
                'documentation': {
                    'recommendation_actions': [action.value for action in RecommendationAction],
                    'automation_statuses': [status.value for status in AutomationStatus],
                    'optimization_approaches': ['pattern', 'cost_based']
                }
            })

    def run(self, host: str = '0.0.0.0', port: int = 8778, debug: bool = False):
        """Run the API server"""
        logger.info(f"Starting automation API server on {host}:{port}")
        self.app.run(host=host, port=port, debug=debug)


def create_automation_server_from_config(config_dict: Dict) -> AutomationAPIServer:
    """
    Create automation server from configuration dictionary

    Args:
        config_dict: Configuration dictionary

    Returns:
        AutomationAPIServer instance
    """
    # Create automation config with proper port handling
    # Check if running inside container (port 5432) or outside (port 15432)
    import os
    is_container = os.path.exists('/.dockerenv')  # Check if running in Docker

    if is_container:
        database_port = 5432  # Inside container
    else:
        database_port = config_dict.get('database_port', 15432)  # Outside container (macOS)

    config_dict['database_port'] = database_port

    # Create automation config
    automation_config = AutomationConfig(**config_dict)

    # Create scheduler
    scheduler = AutomationScheduler(automation_config)

    # Create API server
    return AutomationAPIServer(scheduler)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='NeurDB Automation API Server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8778, help='Port to bind to')
    parser.add_argument('--debug', action='store_true', help='Run in debug mode')
    parser.add_argument('--config', help='Configuration file path')

    args = parser.parse_args()

    # Default configuration
    config = {
        'database_host': 'localhost',
        'database_port': 15432,  # Will be adjusted based on container detection
        'database_name': 'neurdb',
        'database_user': 'postgres',
        'database_password': 'postgres',
        'analysis_interval_minutes': 60,
        'optimization_approach': 'cost_based',
        'recommendation_action': 'report',
        'save_recommendations': True
    }

    # Load configuration from file if provided
    if args.config:
        with open(args.config, 'r') as f:
            file_config = json.load(f)
            config.update(file_config)

    # Create and run server
    server = create_automation_server_from_config(config)
    server.run(host=args.host, port=args.port, debug=args.debug)