#!/usr/bin/env python3
"""
Predictive Index Management Strategy
Implements workload forecasting, clustering, and proactive index optimization
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, asdict
from enum import Enum
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor

from forecasting_pipeline import ForecastingPipeline
from workload_clusterer import OnlineWorkloadClusterer
from cost_based_index_advisor import CostBasedIndexAdvisor, IndexCandidate, QueryCostInfo
from templatizer import AdvancedTemplatizer

logger = logging.getLogger(__name__)


class WorkloadChangeType(Enum):
    """Types of workload changes"""
    NEW_PATTERN = "new_pattern"
    FREQUENCY_SHIFT = "frequency_shift"
    PERFORMANCE_DEGRADATION = "performance_degradation"
    SEASONAL_PATTERN = "seasonal_pattern"
    SIGNIFICANT_CHANGE = "significant_change"


@dataclass
class WorkloadChange:
    """Represents a detected workload change"""
    change_type: WorkloadChangeType
    timestamp: datetime
    confidence: float
    description: str
    affected_queries: List[str]
    performance_impact: float


@dataclass
class PredictiveConfig:
    """Configuration for predictive index management"""
    # Database connection
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "neurdb"
    database_user: str = "neurdb"
    database_password: str = "postgres"

    # Forecasting settings
    forecast_horizon_hours: int = 24
    min_confidence_for_action: float = 0.75
    workload_change_threshold: float = 0.3  # 30% change triggers reoptimization

    # Index management settings
    enable_index_recreation: bool = True  # Delete all indexes and recreate
    storage_budget_mb: float = 1000.0
    max_indexes_to_create: int = 10
    min_index_benefit_threshold: float = 0.1

    # Analysis settings
    analysis_window_hours: int = 24
    clustering_min_cluster_size: int = 5
    forecasting_models: List[str] = None

    def __post_init__(self):
        if self.forecasting_models is None:
            self.forecasting_models = ["arima", "lstm", "prophet", "linear_regression"]


class PredictiveIndexManager:
    """Implements proactive index management based on workload forecasting and clustering"""

    def __init__(self, config: PredictiveConfig):
        self.config = config
        self.db_params = {
            'host': config.database_host,
            'port': config.database_port,
            'database': config.database_name,
            'user': config.database_user,
            'password': config.database_password
        }

        # Initialize components
        self.templatizer = AdvancedTemplatizer()
        self.clusterer = OnlineWorkloadClusterer()
        self.forecasting_pipeline = ForecastingPipeline(self.db_params)
        self.index_advisor = CostBasedIndexAdvisor(self.db_params)

        # State tracking
        self.current_indexes: Dict[str, Dict] = {}
        self.last_optimization_time: Optional[datetime] = None
        self.workload_history: List[Dict] = []
        self.detected_changes: List[WorkloadChange] = []

        logger.info("Predictive Index Manager initialized")

    async def start_monitoring(self):
        """Start the continuous monitoring and optimization loop"""
        logger.info("Starting predictive index monitoring")

        while True:
            try:
                await self.monitoring_cycle()
                await asyncio.sleep(300)  # Check every 5 minutes
            except Exception as e:
                logger.error(f"Error in monitoring cycle: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error

    async def monitoring_cycle(self):
        """Single monitoring and optimization cycle"""
        logger.info("Starting predictive monitoring cycle")

        # 1. Collect current workload data
        workload_data = await self.collect_workload_data()

        # 2. Add to history
        self.workload_history.append({
            'timestamp': datetime.now(),
            'data': workload_data
        })

        # 3. Forecast workload if enough data
        forecast_result = None
        if len(self.workload_history) >= 10:  # Need sufficient history
            forecast_result = await self.forecast_workload()

        # 4. Analyze workload changes
        detected_changes = await self.analyze_workload_changes(forecast_result)

        # 5. Check if reoptimization is needed
        if self.should_reoptimize(detected_changes):
            logger.info("Significant workload change detected, reoptimizing indexes")
            await self.reoptimize_indexes()

        # 6. Update current index state
        await self.update_current_indexes()

        logger.info("Predictive monitoring cycle completed")

    async def collect_workload_data(self) -> Dict:
        """Collect current workload statistics"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                # Get query statistics
                cursor.execute("""
                    SELECT
                        query,
                        calls,
                        total_exec_time,
                        mean_exec_time,
                        rows,
                        100.0 * shared_blks_hit / nullif(shared_blks_hit + shared_blks_read, 0) AS hit_percent
                    FROM pg_stat_statements
                    WHERE calls > 10
                    ORDER BY total_exec_time DESC
                    LIMIT 50
                """)

                queries = cursor.fetchall()

                # Get index usage statistics
                cursor.execute("""
                    SELECT
                        schemaname,
                        tablename,
                        indexname,
                        idx_scan,
                        idx_tup_read,
                        idx_tup_fetch
                    FROM pg_stat_user_indexes
                    ORDER BY idx_scan DESC
                """)

                index_stats = cursor.fetchall()

                return {
                    'timestamp': datetime.now(),
                    'queries': [dict(q) for q in queries],
                    'index_stats': [dict(i) for i in index_stats]
                }

        except Exception as e:
            logger.error(f"Error collecting workload data: {e}")
            return {'timestamp': datetime.now(), 'queries': [], 'index_stats': []}
        finally:
            if 'conn' in locals():
                conn.close()

    async def forecast_workload(self) -> Dict:
        """Forecast future workload patterns"""
        try:
            # Prepare historical data
            historical_data = []
            for entry in self.workload_history[-48:]:  # Last 48 data points
                historical_data.append(entry['data'])

            # Run forecasting pipeline
            forecast_result = await self.forecasting_pipeline.generate_forecast(
                historical_data,
                horizon_hours=self.config.forecast_horizon_hours
            )

            logger.info(f"Generated workload forecast with {len(forecast_result.get('forecasts', []))} predictions")
            return forecast_result

        except Exception as e:
            logger.error(f"Error in workload forecasting: {e}")
            return {}

    async def analyze_workload_changes(self, forecast_result: Dict) -> List[WorkloadChange]:
        """Analyze workload for significant changes"""
        changes = []

        try:
            if not self.workload_history or len(self.workload_history) < 2:
                return changes

            # Compare current workload with previous period
            current_data = self.workload_history[-1]['data']
            previous_data = self.workload_history[-2]['data']

            # Analyze query frequency changes
            current_queries = {q['query']: q['calls'] for q in current_data.get('queries', [])}
            previous_queries = {q['query']: q['calls'] for q in previous_data.get('queries', [])}

            # Detect significant frequency changes
            for query, current_calls in current_queries.items():
                if query in previous_queries:
                    previous_calls = previous_queries[query]
                    if previous_calls > 0:
                        change_ratio = abs(current_calls - previous_calls) / previous_calls

                        if change_ratio > self.config.workload_change_threshold:
                            changes.append(WorkloadChange(
                                change_type=WorkloadChangeType.FREQUENCY_SHIFT,
                                timestamp=datetime.now(),
                                confidence=min(change_ratio, 1.0),
                                description=f"Query frequency changed by {change_ratio:.2%}",
                                affected_queries=[query],
                                performance_impact=change_ratio
                            ))

            # Analyze performance changes
            current_performance = {q['query']: q['mean_exec_time'] for q in current_data.get('queries', [])}
            previous_performance = {q['query']: q['mean_exec_time'] for q in previous_data.get('queries', [])}

            for query, current_time in current_performance.items():
                if query in previous_performance:
                    previous_time = previous_performance[query]
                    if previous_time > 0:
                        performance_change = (current_time - previous_time) / previous_time

                        if performance_change > 0.2:  # 20% performance degradation
                            changes.append(WorkloadChange(
                                change_type=WorkloadChangeType.PERFORMANCE_DEGRADATION,
                                timestamp=datetime.now(),
                                confidence=min(abs(performance_change), 1.0),
                                description=f"Query performance degraded by {performance_change:.2%}",
                                affected_queries=[query],
                                performance_impact=abs(performance_change)
                            ))

            # Analyze forecast for future changes
            if forecast_result:
                forecast_changes = await self.analyze_forecast_changes(forecast_result)
                changes.extend(forecast_changes)

            self.detected_changes = changes
            logger.info(f"Detected {len(changes)} workload changes")
            return changes

        except Exception as e:
            logger.error(f"Error analyzing workload changes: {e}")
            return []

    async def analyze_forecast_changes(self, forecast_result: Dict) -> List[WorkloadChange]:
        """Analyze forecast results for future workload changes"""
        changes = []

        try:
            forecasts = forecast_result.get('forecasts', [])

            for forecast in forecasts:
                if forecast.get('confidence', 0) > self.config.min_confidence_for_action:
                    # Check if forecast predicts significant changes
                    predicted_volume = forecast.get('predicted_volume', 0)
                    current_volume = forecast.get('current_volume', 0)

                    if current_volume > 0:
                        volume_change = abs(predicted_volume - current_volume) / current_volume

                        if volume_change > self.config.workload_change_threshold:
                            changes.append(WorkloadChange(
                                change_type=WorkloadChangeType.SIGNIFICANT_CHANGE,
                                timestamp=datetime.now(),
                                confidence=forecast.get('confidence', 0),
                                description=f"Forecast predicts {volume_change:.2%} volume change",
                                affected_queries=forecast.get('affected_queries', []),
                                performance_impact=volume_change
                            ))

            return changes

        except Exception as e:
            logger.error(f"Error analyzing forecast changes: {e}")
            return []

    def should_reoptimize(self, changes: List[WorkloadChange]) -> bool:
        """Determine if index reoptimization should be triggered"""
        if not changes:
            return False

        # Check if any change meets threshold
        for change in changes:
            if (change.confidence >= self.config.min_confidence_for_action and
                change.performance_impact >= self.config.workload_change_threshold):
                return True

        # Check if it's been long enough since last optimization
        if self.last_optimization_time:
            hours_since_last = (datetime.now() - self.last_optimization_time).total_seconds() / 3600
            if hours_since_last >= 24:  # At least daily reoptimization
                return True

        return False

    async def reoptimize_indexes(self):
        """Perform complete index reoptimization using DTA approach"""
        logger.info("Starting complete index reoptimization")

        try:
            # 1. Get current workload queries for analysis
            workload_queries = await self.get_workload_queries_for_analysis()

            if not workload_queries:
                logger.warning("No workload queries available for analysis")
                return

            # 2. Run cost-based index advisor (DTA-like approach)
            index_recommendations = await self.index_advisor.optimize_workload(
                workload_queries,
                storage_budget_mb=self.config.storage_budget_mb
            )

            # 3. Delete all existing auto-managed indexes
            if self.config.enable_index_recreation:
                await self.delete_all_auto_indexes()

            # 4. Create new recommended indexes
            created_indexes = []
            for recommendation in index_recommendations[:self.config.max_indexes_to_create]:
                if recommendation.benefit_score >= self.config.min_index_benefit_threshold:
                    success = await self.create_index_from_recommendation(recommendation)
                    if success:
                        created_indexes.append(recommendation)

            # 5. Update last optimization time
            self.last_optimization_time = datetime.now()

            logger.info(f"Reoptimization completed: created {len(created_indexes)} new indexes")

        except Exception as e:
            logger.error(f"Error in index reoptimization: {e}")
            raise

    async def get_workload_queries_for_analysis(self) -> List[str]:
        """Get current workload queries for analysis"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT query
                    FROM pg_stat_statements
                    WHERE calls > 5
                    ORDER BY total_exec_time DESC
                    LIMIT 20
                """)

                return [row[0] for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error getting workload queries: {e}")
            return []
        finally:
            if 'conn' in locals():
                conn.close()

    async def delete_all_auto_indexes(self):
        """Delete all auto-managed indexes (idx_auto_ prefix)"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Get all auto-managed indexes
                cursor.execute("""
                    SELECT indexname, tablename
                    FROM pg_indexes
                    WHERE indexname LIKE 'idx_auto_%'
                """)

                auto_indexes = cursor.fetchall()

                for index_name, table_name in auto_indexes:
                    try:
                        cursor.execute(f"DROP INDEX IF EXISTS {index_name}")
                        logger.info(f"Dropped auto-index: {index_name}")
                    except Exception as e:
                        logger.warning(f"Failed to drop index {index_name}: {e}")

                conn.commit()
                logger.info(f"Dropped {len(auto_indexes)} auto-managed indexes")

        except Exception as e:
            logger.error(f"Error deleting auto indexes: {e}")
        finally:
            if 'conn' in locals():
                conn.close()

    async def create_index_from_recommendation(self, recommendation) -> bool:
        """Create an index from a cost-based advisor recommendation"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Enable auto-index creation for this session
                cursor.execute("SET nr_enable_auto_index_creation = true;")

                # Build index name
                index_name = f"idx_auto_{recommendation.table_name}_{recommendation.columns[0]}_{int(time.time())}"

                # Create the index using the budget-aware function
                success = cursor.execute("""
                    SELECT nr_create_index_if_budget_allows(%s, %s, %s, %s)
                """, (
                    index_name,
                    recommendation.table_name,
                    recommendation.columns,
                    recommendation.index_type.value
                )).fetchone()[0]

                conn.commit()

                if success:
                    logger.info(f"Created predictive index: {index_name}")
                    return True
                else:
                    logger.warning(f"Failed to create index due to budget: {index_name}")
                    return False

        except Exception as e:
            logger.error(f"Error creating index {index_name}: {e}")
            return False
        finally:
            if 'conn' in locals():
                conn.close()

    async def update_current_indexes(self):
        """Update the current index state tracking"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT indexname, tablename, schemaname, indexdef
                    FROM pg_indexes
                    WHERE indexname LIKE 'idx_auto_%'
                """)

                self.current_indexes = {
                    row['indexname']: {
                        'table': row['tablename'],
                        'schema': row['schemaname'],
                        'definition': row['indexdef']
                    }
                    for row in cursor.fetchall()
                }

                logger.info(f"Currently tracking {len(self.current_indexes)} auto-managed indexes")

        except Exception as e:
            logger.error(f"Error updating current indexes: {e}")
        finally:
            if 'conn' in locals():
                conn.close()

    def get_status(self) -> Dict:
        """Get current status of predictive index management"""
        return {
            'strategy': 'predictive',
            'last_optimization': self.last_optimization_time.isoformat() if self.last_optimization_time else None,
            'current_indexes_count': len(self.current_indexes),
            'workload_history_size': len(self.workload_history),
            'detected_changes_count': len(self.detected_changes),
            'detected_changes': [
                {
                    'type': change.change_type.value,
                    'confidence': change.confidence,
                    'description': change.description,
                    'performance_impact': change.performance_impact
                }
                for change in self.detected_changes[-5:]  # Last 5 changes
            ]
        }

    def get_index_recommendations(self) -> List[Dict]:
        """Get current index recommendations"""
        try:
            # Run analysis on current workload
            workload_queries = asyncio.create_task(self.get_workload_queries_for_analysis())

            # This is a simplified version - in practice would run full DTA analysis
            recommendations = []

            # For now, return basic recommendations based on current indexes
            for index_name, index_info in self.current_indexes.items():
                recommendations.append({
                    'table_name': index_info['table'],
                    'index_name': index_name,
                    'index_type': 'btree',
                    'benefit_score': 0.7,  # Would be calculated from actual usage
                    'estimated_size_mb': 5.0,
                    'recommendation_type': 'maintain',
                    'reason': 'Current predictive index from workload analysis'
                })

            return recommendations

        except Exception as e:
            logger.error(f"Error getting index recommendations: {e}")
            return []