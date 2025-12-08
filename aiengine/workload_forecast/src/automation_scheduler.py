#!/usr/bin/env python3
"""
Automation Scheduler for NeurDB Workload Forecast
Handles periodic analysis and automated index recommendations
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import threading
import schedule
from pathlib import Path

from forecast_index_service import ForecastIndexService, ForecastIndexConfig, ServiceMode
from data_collector import WorkloadDataCollector

logger = logging.getLogger(__name__)


class AutomationStatus(Enum):
    """Current status of the automation system"""
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class RecommendationAction(Enum):
    """What to do with generated recommendations"""
    NONE = "none"  # Just generate recommendations
    REPORT = "report"  # Generate report and notify
    SUGGEST = "suggest"  # Create recommendations for review
    AUTO_APPLY_SAFE = "auto_apply_safe"  # Auto-apply only high-confidence, low-risk indexes
    AUTO_APPLY_ALL = "auto_apply_all"  # Auto-apply all recommendations (use with caution)


@dataclass
class AutomationConfig:
    """Configuration for the automation scheduler"""
    # Database connection
    database_host: str = "localhost"
    database_port: int = 15432
    database_name: str = "neurdb"
    database_user: str = "postgres"
    database_password: str = "postgres"

    # Scheduling configuration
    analysis_interval_minutes: int = 60  # How often to run analysis
    data_collection_window_hours: int = 24  # How much historical data to analyze
    forecast_horizon_hours: int = 24  # How far ahead to forecast

    # Recommendation settings
    optimization_approach: str = "cost_based"  # "pattern" or "cost_based"
    recommendation_action: RecommendationAction = RecommendationAction.REPORT
    confidence_threshold: float = 0.7  # Minimum confidence for auto-apply
    max_indexes_per_run: int = 5  # Safety limit for automated index creation
    require_approval: bool = True  # Require human approval for index creation

    # Performance and safety
    max_creation_time_seconds: int = 300  # Max time for index creation
    min_performance_improvement: float = 0.1  # 10% minimum improvement
    max_storage_overhead_mb: int = 1000  # Maximum storage overhead

    # Monitoring and alerts
    enable_monitoring: bool = True
    alert_on_errors: bool = True
    alert_on_recommendations: bool = True
    alert_webhook_url: Optional[str] = None

    # Logging and persistence
    log_level: str = "INFO"
    save_recommendations: bool = True
    recommendations_file: str = "/tmp/neurdb_recommendations.json"
    metrics_file: str = "/tmp/neurdb_automation_metrics.json"


@dataclass
class AutomationMetrics:
    """Metrics for the automation system"""
    start_time: datetime
    last_analysis_time: Optional[datetime] = None
    total_analyses: int = 0
    total_recommendations: int = 0
    total_indexes_created: int = 0
    total_errors: int = 0
    average_analysis_time: float = 0.0
    last_error: Optional[str] = None
    uptime_hours: float = 0.0

    def update_uptime(self):
        """Update uptime calculation"""
        self.uptime_hours = (datetime.now() - self.start_time).total_seconds() / 3600

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        # Convert datetime objects to strings
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat() if value else None
        return data


class AutomationScheduler:
    """
    Main automation scheduler for periodic workload analysis and index recommendations
    """

    def __init__(self, config: AutomationConfig):
        """
        Initialize the automation scheduler

        Args:
            config: Automation configuration
        """
        self.config = config
        self.status = AutomationStatus.STOPPED
        self.metrics = AutomationMetrics(start_time=datetime.now())

        # Initialize services
        self.forecast_service = None
        self.data_collector = None

        # Scheduler and threading
        self.scheduler = schedule.Scheduler()
        self.scheduler_thread = None
        self.stop_event = threading.Event()

        # Callbacks for custom actions
        self.on_recommendation_generated: Optional[Callable] = None
        self.on_index_created: Optional[Callable] = None
        self.on_error: Optional[Callable] = None

        # History tracking
        self.recommendation_history: List[Dict] = []

        logger.info(f"Automation scheduler initialized with {config.analysis_interval_minutes}min interval")

    async def initialize(self):
        """Initialize all services and connections"""
        try:
            # Initialize data collector
            self.data_collector = WorkloadDataCollector({
                'host': self.config.database_host,
                'port': self.config.database_port,
                'database': self.config.database_name,
                'user': self.config.database_user,
                'password': self.config.database_password
            })
            await self.data_collector.initialize()
            logger.info("Data collector initialized")

            # Initialize forecast index service
            service_config = ForecastIndexConfig(
                database_host=self.config.database_host,
                database_port=self.config.database_port,
                database_name=self.config.database_name,
                database_user=self.config.database_user,
                database_password=self.config.database_password,
                mode=ServiceMode.SCHEDULED,
                schedule_interval_minutes=self.config.analysis_interval_minutes,
                optimization_approach=self.config.optimization_approach,
                forecast_horizon_hours=self.config.forecast_horizon_hours,
                max_recommendations=self.config.max_indexes_per_run,
                enable_hypothetical_analysis=True,
                cost_based_time_limit_seconds=self.config.analysis_interval_minutes * 0.8,  # Use 80% of interval for optimization
                cost_based_max_indexes_per_table=2
            )

            self.forecast_service = ForecastIndexService(service_config)
            await self.forecast_service.initialize()
            await self.forecast_service.start()
            logger.info("Forecast index service initialized")

        except Exception as e:
            logger.error(f"Failed to initialize automation scheduler: {e}")
            raise

    def start(self):
        """Start the automation scheduler"""
        if self.status == AutomationStatus.RUNNING:
            logger.warning("Automation scheduler is already running")
            return

        try:
            # Schedule periodic analysis
            self.scheduler.every(self.config.analysis_interval_minutes).minutes.do(
                self._run_periodic_analysis
            )

            # Schedule metrics updates
            self.scheduler.every(5).minutes.do(self._update_metrics)

            # Start scheduler in background thread
            self.stop_event.clear()
            self.scheduler_thread = threading.Thread(
                target=self._run_scheduler,
                daemon=True,
                name="AutomationScheduler"
            )
            self.scheduler_thread.start()

            self.status = AutomationStatus.RUNNING
            self.metrics.start_time = datetime.now()

            logger.info(f"Automation scheduler started - running analysis every {self.config.analysis_interval_minutes} minutes")

            # Run first analysis immediately
            asyncio.create_task(self._run_periodic_analysis_async())

        except Exception as e:
            logger.error(f"Failed to start automation scheduler: {e}")
            self.status = AutomationStatus.ERROR
            raise

    def stop(self):
        """Stop the automation scheduler"""
        if self.status != AutomationStatus.RUNNING:
            return

        logger.info("Stopping automation scheduler...")

        self.status = AutomationStatus.STOPPED
        self.stop_event.set()

        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=10)

        # Stop forecast service
        if self.forecast_service:
            asyncio.create_task(self.forecast_service.stop())

        logger.info("Automation scheduler stopped")

    def pause(self):
        """Pause the automation scheduler"""
        if self.status == AutomationStatus.RUNNING:
            self.status = AutomationStatus.PAUSED
            logger.info("Automation scheduler paused")

    def resume(self):
        """Resume the automation scheduler"""
        if self.status == AutomationStatus.PAUSED:
            self.status = AutomationStatus.RUNNING
            logger.info("Automation scheduler resumed")

    def _run_scheduler(self):
        """Run the scheduler in a background thread"""
        while not self.stop_event.is_set():
            try:
                self.scheduler.run_pending()
                time.sleep(1)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                if self.config.alert_on_errors:
                    self._send_alert("Scheduler Error", str(e))

    async def _run_periodic_analysis_async(self):
        """Async wrapper for periodic analysis"""
        try:
            await self._run_periodic_analysis()
        except Exception as e:
            logger.error(f"Error in periodic analysis: {e}")
            self.metrics.total_errors += 1
            self.metrics.last_error = str(e)

            if self.on_error:
                self.on_error(e)

    def _run_periodic_analysis(self):
        """Run a complete analysis cycle"""
        if self.status != AutomationStatus.RUNNING:
            return

        logger.info("Starting periodic workload analysis...")
        start_time = time.time()

        try:
            # Run the analysis in asyncio event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                result = loop.run_until_complete(
                    self._perform_analysis_cycle()
                )

                # Process recommendations based on action configuration
                if result:
                    loop.run_until_complete(
                        self._process_recommendations(result)
                    )

            finally:
                loop.close()

        except Exception as e:
            logger.error(f"Periodic analysis failed: {e}")
            self.metrics.total_errors += 1
            self.metrics.last_error = str(e)
            self.status = AutomationStatus.ERROR

            if self.config.alert_on_errors:
                self._send_alert("Analysis Error", str(e))
        else:
            # Update metrics on success
            analysis_time = time.time() - start_time
            self.metrics.last_analysis_time = datetime.now()
            self.metrics.total_analyses += 1

            # Update average analysis time
            if self.metrics.total_analyses == 1:
                self.metrics.average_analysis_time = analysis_time
            else:
                self.metrics.average_analysis_time = (
                    (self.metrics.average_analysis_time * (self.metrics.total_analyses - 1) + analysis_time) /
                    self.metrics.total_analyses
                )

        finally:
            self._update_metrics()
            logger.info(f"Periodic analysis completed in {time.time() - start_time:.2f} seconds")

    async def _perform_analysis_cycle(self):
        """Perform a complete analysis and recommendation cycle"""
        # Calculate time windows
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=self.config.data_collection_window_hours)

        logger.info(f"Analyzing workload from {start_time} to {end_time}")

        # Generate recommendations
        result = await self.forecast_service.generate_recommendations(
            start_time=start_time,
            end_time=end_time,
            force_refresh=True
        )

        if result and result.recommendations:
            self.metrics.total_recommendations += len(result.recommendations)
            logger.info(f"Generated {len(result.recommendations)} recommendations")

            # Store recommendation history
            history_entry = {
                'timestamp': result.timestamp.isoformat(),
                'analysis_period_hours': result.analysis_period_hours,
                'recommendations_count': len(result.recommendations),
                'high_confidence_count': result.high_confidence_recommendations,
                'estimated_cost_savings': result.estimated_cost_savings
            }
            self.recommendation_history.append(history_entry)

            # Keep only last 100 entries
            if len(self.recommendation_history) > 100:
                self.recommendation_history = self.recommendation_history[-100:]

        return result

    async def _process_recommendations(self, result):
        """Process generated recommendations based on action configuration"""
        if not result or not result.recommendations:
            logger.info("No recommendations to process")
            return

        recommendations = result.recommendations

        if self.config.recommendation_action == RecommendationAction.NONE:
            logger.info("Recommendations generated but no action configured")
            return

        elif self.config.recommendation_action == RecommendationAction.REPORT:
            await self._generate_report(recommendations)

        elif self.config.recommendation_action == RecommendationAction.SUGGEST:
            await self._create_suggestions(recommendations)

        elif self.config.recommendation_action in [RecommendationAction.AUTO_APPLY_SAFE, RecommendationAction.AUTO_APPLY_ALL]:
            await self._auto_apply_recommendations(recommendations)

        # Call custom callback if provided
        if self.on_recommendation_generated:
            self.on_recommendation_generated(recommendations)

        # Send alert if configured
        if self.config.alert_on_recommendations:
            self._send_alert(
                "New Index Recommendations",
                f"Generated {len(recommendations)} new recommendations with {result.high_confidence_recommendations} high confidence"
            )

    async def _generate_report(self, recommendations):
        """Generate and save a detailed report"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'total_recommendations': len(recommendations),
            'recommendations': []
        }

        for rec in recommendations:
            report['recommendations'].append({
                'table': rec.candidate.table_name,
                'columns': rec.candidate.columns,
                'index_type': rec.candidate.index_type.value,
                'strength': rec.recommendation_strength,
                'confidence': rec.candidate.confidence_score,
                'estimated_benefit': rec.candidate.estimated_benefit,
                'creation_cost': rec.candidate.creation_cost,
                'sql': rec.candidate.sql_statements[0] if rec.candidate.sql_statements else None
            })

        # Save report
        if self.config.save_recommendations:
            with open(self.config.recommendations_file, 'w') as f:
                json.dump(report, f, indent=2)
            logger.info(f"Recommendation report saved to {self.config.recommendations_file}")

    async def _create_suggestions(self, recommendations):
        """Create recommendations for review (but don't auto-apply)"""
        logger.info(f"Creating {len(recommendations)} suggestions for review")

        # In a real implementation, this might:
        # 1. Store suggestions in a database table for review
        # 2. Send emails to DBAs
        # 3. Create tickets in a tracking system
        # 4. Update a dashboard

        await self._generate_report(recommendations)

    async def _auto_apply_recommendations(self, recommendations):
        """Automatically apply safe recommendations"""
        if self.config.recommendation_action == RecommendationAction.AUTO_APPLY_SAFE:
            # Filter for safe recommendations only
            safe_recommendations = [
                rec for rec in recommendations
                if (rec.candidate.confidence_score >= self.config.confidence_threshold and
                    rec.risk_level in ['LOW', 'MEDIUM'] and
                    rec.candidate.estimated_benefit >= self.config.min_performance_improvement)
            ]
            logger.info(f"Filtered to {len(safe_recommendations)} safe recommendations from {len(recommendations)} total")
        else:
            # Auto-apply all recommendations (use with caution!)
            safe_recommendations = recommendations
            logger.warning(f"Auto-applying ALL {len(recommendations)} recommendations (verify this is intended)")

        # Limit number of indexes created per run
        safe_recommendations = safe_recommendations[:self.config.max_indexes_per_run]

        # Apply recommendations
        for rec in safe_recommendations:
            try:
                if self.config.require_approval:
                    logger.info(f"Would create index: {rec.candidate.sql_statements[0] if rec.candidate.sql_statements else 'N/A'}")
                    continue

                # Actually create the index
                success = await self._create_index(rec)
                if success:
                    self.metrics.total_indexes_created += 1
                    logger.info(f"Created index: {rec.candidate.table_name}({', '.join(rec.candidate.columns)})")

                    if self.on_index_created:
                        self.on_index_created(rec)
                else:
                    logger.warning(f"Failed to create index: {rec.candidate.table_name}")

            except Exception as e:
                logger.error(f"Error creating index {rec.candidate.table_name}: {e}")
                self.metrics.total_errors += 1

    async def _create_index(self, recommendation):
        """Create an index in the database"""
        if not self.forecast_service:
            return False

        # In a real implementation, this would:
        # 1. Execute the CREATE INDEX statement
        # 2. Wait for creation (with timeout)
        # 3. Verify the index was created successfully
        # 4. Handle errors appropriately

        # For now, just log what would be created
        sql = recommendation.candidate.sql_statements[0] if recommendation.candidate.sql_statements else None
        if sql:
            logger.info(f"SQL to execute: {sql}")
            # In production: await self.db_connection.execute(sql)
            return True
        return False

    def _update_metrics(self):
        """Update system metrics"""
        self.metrics.update_uptime()

        # Save metrics to file
        if self.config.save_recommendations:
            try:
                with open(self.config.metrics_file, 'w') as f:
                    json.dump(self.metrics.to_dict(), f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save metrics: {e}")

    def _send_alert(self, title: str, message: str):
        """Send an alert (webhook, email, etc.)"""
        logger.info(f"ALERT: {title} - {message}")

        if self.config.alert_webhook_url:
            try:
                import requests
                payload = {
                    'title': title,
                    'message': message,
                    'timestamp': datetime.now().isoformat(),
                    'service': 'neurdb-automation'
                }
                requests.post(self.config.alert_webhook_url, json=payload, timeout=10)
            except Exception as e:
                logger.warning(f"Failed to send webhook alert: {e}")

    def get_status(self) -> Dict:
        """Get current automation status"""
        self.metrics.update_uptime()
        return {
            'status': self.status.value,
            'metrics': self.metrics.to_dict(),
            'config': {
                'analysis_interval_minutes': self.config.analysis_interval_minutes,
                'optimization_approach': self.config.optimization_approach,
                'recommendation_action': self.config.recommendation_action.value,
                'next_analysis': (self.scheduler.next_run() if self.scheduler.jobs else None)
            },
            'recommendation_history_count': len(self.recommendation_history)
        }

    def get_recommendation_history(self, limit: int = 10) -> List[Dict]:
        """Get recent recommendation history"""
        return self.recommendation_history[-limit:] if self.recommendation_history else []