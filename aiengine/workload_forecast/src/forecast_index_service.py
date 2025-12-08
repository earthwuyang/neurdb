"""
Forecast Index Service for NeurDB
Integrates workload forecasting with index recommendation pipeline
"""

import asyncio
import json
import logging
from typing import Dict, List, Tuple, Set, Optional, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import numpy as np
from collections import defaultdict

from index_recommendation_engine import (
    IndexRecommendationEngine, RecommendationConfig, RecommendationStrategy,
    RecommendationResult
)
from index_advisor import QueryAnalysis, WorkloadIndexAdvisor
from cost_based_index_advisor import CostBasedIndexAdvisor
from forecasting_pipeline import ForecastingPipeline
from data_collector import WorkloadDataCollector, WorkloadMetrics

logger = logging.getLogger(__name__)


class ServiceMode(Enum):
    """Service operation modes"""
    ON_DEMAND = "on_demand"  # Generate recommendations on request
    SCHEDULED = "scheduled"  # Periodic recommendations
    STREAMING = "streaming"  # Real-time continuous recommendations


@dataclass
class ForecastIndexConfig:
    """Configuration for forecast-index service"""
    # Database connection
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "neurdb"
    database_user: str = "postgres"
    database_password: str = "postgres"

    # Service mode
    mode: ServiceMode = ServiceMode.ON_DEMAND
    schedule_interval_minutes: int = 60  # For scheduled mode

    # Forecasting configuration
    forecast_horizon_hours: int = 24
    forecasting_models: List[str] = None  # Will use default if None
    min_forecast_confidence: float = 0.7

    # Index recommendation configuration
    recommendation_strategy: RecommendationStrategy = RecommendationStrategy.BALANCED
    optimization_approach: str = "pattern"  # "pattern" or "cost_based"
    max_recommendations: int = 50
    enable_hypothetical_analysis: bool = True
    require_forecast_validation: bool = False  # Don't require forecasts for recommendations

    # Cost-based optimization configuration
    cost_based_time_limit_seconds: int = 60
    cost_based_max_indexes_per_table: int = 3
    cost_based_progressive_optimization: bool = True

    # Data collection configuration
    lookback_hours: int = 168  # 1 week
    min_template_frequency: int = 5  # Minimum executions for template consideration
    max_templates_to_analyze: int = 100

    # Performance configuration
    max_concurrent_tasks: int = 5
    cache_ttl_hours: int = 24
    enable_async_processing: bool = True

    def __post_init__(self):
        if self.forecasting_models is None:
            self.forecasting_models = ["AR", "RNN", "Spectral", "Ensemble"]


@dataclass
class ForecastIndexResult:
    """Complete forecast-index analysis result"""
    timestamp: datetime
    analysis_period_hours: int
    forecast_horizon_hours: int
    total_templates_analyzed: int
    total_recommendations_generated: int
    high_confidence_recommendations: int
    estimated_total_impact_queries: int
    estimated_cost_savings: float
    recommendations: List[RecommendationResult]
    forecast_summary: Dict[str, Any]
    workload_summary: Dict[str, Any]


class ForecastIndexService:
    """
    Service that integrates workload forecasting with index recommendations
    to provide proactive performance optimization suggestions
    """

    def __init__(self, config: ForecastIndexConfig):
        """
        Initialize the forecast-index service

        Args:
            config: Service configuration
        """
        self.config = config

        # Initialize components
        self._initialize_components()

        # Internal state
        self.is_initialized = False
        self.is_running = False
        self.last_analysis_time = None
        self.scheduled_task = None
        self.result_cache = {}

        logger.info(f"ForecastIndexService initialized in {config.mode.value} mode")

    def _initialize_components(self):
        """Initialize all service components"""
        # Data collector
        self.data_collector = WorkloadDataCollector({
            'host': self.config.database_host,
            'port': self.config.database_port,
            'database': self.config.database_name,
            'user': self.config.database_user,
            'password': self.config.database_password
        })

        # Forecasting pipeline
        forecast_config = {
            'models_dir': 'models/forecasting',
            'retrain_interval_hours': 24,
            'min_training_samples': 50,
            'validation_split': 0.2,
            'enable_auto_retraining': True,
            'database': {
                'host': self.config.database_host,
                'port': self.config.database_port,
                'database': self.config.database_name,
                'user': self.config.database_user,
                'password': self.config.database_password
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
        self.forecasting_pipeline = ForecastingPipeline(forecast_config)

        # Index recommendation engine
        recommendation_config = RecommendationConfig(
            database_host=self.config.database_host,
            database_port=self.config.database_port,
            database_name=self.config.database_name,
            database_user=self.config.database_user,
            database_password=self.config.database_password,
            strategy=self.config.recommendation_strategy,
            max_recommendations=self.config.max_recommendations,
            enable_hypothetical_analysis=self.config.enable_hypothetical_analysis,
            forecast_horizon_hours=self.config.forecast_horizon_hours,
            include_forecast_impact=True
        )
        self.recommendation_engine = IndexRecommendationEngine(recommendation_config)

        # Initialize cost-based advisor if configured
        if self.config.optimization_approach == "cost_based":
            cost_based_db_params = {
                'host': self.config.database_host,
                'port': self.config.database_port,
                'database': self.config.database_name,
                'user': self.config.database_user,
                'password': self.config.database_password
            }
            self.cost_based_advisor = CostBasedIndexAdvisor(cost_based_db_params)

    async def initialize(self):
        """Initialize the service and all components"""
        try:
            # Initialize data collector
            await self.data_collector.initialize()

            # Initialize forecasting pipeline
            await self.forecasting_pipeline.initialize()

            # Initialize recommendation engine
            schema_info = await self._collect_schema_info()
            await self.recommendation_engine.initialize(schema_info)

            self.is_initialized = True
            logger.info("ForecastIndexService initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize ForecastIndexService: {e}")
            raise

    async def start(self):
        """Start the service (for scheduled/streaming modes)"""
        if not self.is_initialized:
            await self.initialize()

        if self.config.mode == ServiceMode.SCHEDULED:
            await self._start_scheduled_mode()
        elif self.config.mode == ServiceMode.STREAMING:
            await self._start_streaming_mode()

        self.is_running = True
        logger.info(f"ForecastIndexService started in {self.config.mode.value} mode")

    async def stop(self):
        """Stop the service"""
        if self.scheduled_task:
            self.scheduled_task.cancel()
            try:
                await self.scheduled_task
            except asyncio.CancelledError:
                pass

        await self.recommendation_engine.cleanup()
        self.is_running = False
        logger.info("ForecastIndexService stopped")

    async def generate_recommendations(self,
                                     start_time: Optional[datetime] = None,
                                     end_time: Optional[datetime] = None,
                                     force_refresh: bool = False) -> ForecastIndexResult:
        """
        Generate index recommendations based on workload forecasts

        Args:
            start_time: Analysis start time (defaults to lookback_hours ago)
            end_time: Analysis end time (defaults to now)
            force_refresh: Force new analysis even if cached results exist

        Returns:
            Complete forecast-index analysis result
        """
        if not self.is_initialized:
            await self.initialize()

        # Set default time window
        if end_time is None:
            end_time = datetime.now()
        if start_time is None:
            start_time = end_time - timedelta(hours=self.config.lookback_hours)

        # Check cache
        cache_key = f"{start_time.isoformat()}_{end_time.isoformat()}"
        if not force_refresh and self._is_cache_valid(cache_key):
            logger.info("Returning cached recommendations")
            return self.result_cache[cache_key]

        logger.info(f"Generating recommendations for period {start_time} to {end_time}")

        try:
            # Step 1: Collect workload data
            workload_data = await self._collect_workload_data(start_time, end_time)

            # Step 2: Analyze query templates
            query_analyses = await self._analyze_query_templates(workload_data)

            # Step 3: Generate forecasts
            forecasts = await self._generate_forecasts(workload_data, query_analyses)

            # Step 4: Generate index recommendations
            recommendations = await self._generate_index_recommendations(
                query_analyses, forecasts, workload_data
            )

            # Step 5: Create comprehensive result
            result = await self._create_comprehensive_result(
                start_time, end_time, query_analyses, forecasts, recommendations, workload_data
            )

            # Cache result
            self.result_cache[cache_key] = result
            self.last_analysis_time = datetime.now()

            logger.info(f"Generated {len(recommendations)} recommendations")
            return result

        except Exception as e:
            logger.error(f"Failed to generate recommendations: {e}")
            raise

    async def _collect_schema_info(self) -> Dict[str, Dict]:
        """Collect database schema information"""
        try:
            # This would typically query the database schema
            # For now, return empty dict - schema info is optional
            logger.info("Schema collection not fully implemented - using empty schema")
            return {}
        except Exception as e:
            logger.warning(f"Failed to collect schema info: {e}")
            return {}

    async def _collect_workload_data(self, start_time: datetime, end_time: datetime) -> List[WorkloadMetrics]:
        """Collect workload metrics for the analysis period"""
        try:
            # Collect metrics from database
            workload_data = await self.data_collector.collect_metrics(
                start_time=start_time,
                end_time=end_time,
                metrics_types=['execution_time', 'execution_count', 'cpu_usage']
            )

            logger.info(f"Collected {len(workload_data)} workload metrics")
            return workload_data

        except Exception as e:
            logger.error(f"Failed to collect workload data: {e}")
            # Return empty list to continue with other steps
            return []

    async def _analyze_query_templates(self, workload_data: List[WorkloadMetrics]) -> List[QueryAnalysis]:
        """Analyze query templates from workload data"""
        try:
            # Extract query templates from workload data
            template_data = defaultdict(list)

            for metric in workload_data:
                if hasattr(metric, 'template_hash') and metric.template_hash:
                    template_data[metric.template_hash].append(metric)

            # Analyze templates
            query_analyses = []
            advisor = WorkloadIndexAdvisor()

            for template_hash, metrics in template_data.items():
                if len(metrics) >= self.config.min_template_frequency:
                    # Calculate template statistics
                    execution_count = len(metrics)
                    avg_execution_time = np.mean([m.execution_time for m in metrics if hasattr(m, 'execution_time')])

                    # Get template text (simplified)
                    template_text = f"TEMPLATE_{template_hash}"  # Would be actual template text

                    # Create workload data dict
                    workload_dict = {
                        'execution_count': execution_count,
                        'avg_execution_time_ms': avg_execution_time,
                        'metrics': metrics
                    }

                    # Analyze template
                    analysis = advisor.analyze_query_template(template_hash, template_text, workload_dict)
                    query_analyses.append(analysis)

            # Limit number of templates to analyze
            if len(query_analyses) > self.config.max_templates_to_analyze:
                # Sort by execution frequency and take top N
                query_analyses.sort(key=lambda x: x.execution_frequency, reverse=True)
                query_analyses = query_analyses[:self.config.max_templates_to_analyze]

            logger.info(f"Analyzed {len(query_analyses)} query templates")
            return query_analyses

        except Exception as e:
            logger.error(f"Failed to analyze query templates: {e}")
            return []

    async def _generate_forecasts(self, workload_data: List[WorkloadMetrics],
                                query_analyses: List[QueryAnalysis]) -> Dict[str, List[Tuple[datetime, float]]]:
        """Generate forecasts for query templates"""
        try:
            forecasts = {}

            for analysis in query_analyses:
                # Get time series data for this template
                template_metrics = [
                    m for m in workload_data
                    if hasattr(m, 'template_hash') and m.template_hash == analysis.template_hash
                ]

                if template_metrics:
                    # Create time series
                    time_series = []
                    for metric in template_metrics:
                        if hasattr(metric, 'timestamp') and hasattr(metric, 'execution_count'):
                            time_series.append((metric.timestamp, float(metric.execution_count)))

                    # Sort by timestamp
                    time_series.sort(key=lambda x: x[0])

                    if len(time_series) >= 10:  # Minimum data points for forecasting
                        # Generate forecast
                        forecast = await self.forecasting_pipeline.forecast_template(
                            analysis.template_hash, time_series
                        )

                        if forecast:
                            forecasts[analysis.template_hash] = forecast

            logger.info(f"Generated forecasts for {len(forecasts)} templates")
            return forecasts

        except Exception as e:
            logger.error(f"Failed to generate forecasts: {e}")
            return {}

    async def _generate_index_recommendations(self,
                                            query_analyses: List[QueryAnalysis],
                                            forecasts: Dict[str, List[Tuple[datetime, float]]],
                                            workload_data: List[WorkloadMetrics]) -> List[RecommendationResult]:
        """Generate index recommendations based on analysis and forecasts"""
        try:
            if self.config.optimization_approach == "cost_based":
                return await self._generate_cost_based_recommendations(
                    query_analyses, forecasts, workload_data
                )
            else:
                # Default pattern-based approach
                recommendations = await self.recommendation_engine.generate_recommendations(
                    query_analyses, forecasts
                )
                logger.info(f"Generated {len(recommendations)} pattern-based index recommendations")
                return recommendations

        except Exception as e:
            logger.error(f"Failed to generate index recommendations: {e}")
            return []

    async def _generate_cost_based_recommendations(self,
                                                  query_analyses: List[QueryAnalysis],
                                                  forecasts: Dict[str, List[Tuple[datetime, float]]],
                                                  workload_data: List[WorkloadMetrics]) -> List[RecommendationResult]:
        """Generate cost-based index recommendations using hypopg"""
        try:
            # Extract queries from analyses
            workload_queries = []
            for analysis in query_analyses:
                # Add the original query multiple times based on frequency
                frequency = max(1, int(analysis.execution_count))
                for _ in range(min(frequency, 10)):  # Cap at 10 to avoid too many repetitions
                    workload_queries.append(analysis.query_template)

            # Add forecast-weighted queries
            for template_id, forecast_data in forecasts.items():
                if forecast_data:
                    # Find the corresponding analysis
                    matching_analysis = next(
                        (a for a in query_analyses if a.template_id == template_id), None
                    )
                    if matching_analysis:
                        # Weight by forecast values
                        avg_forecast = sum(value for _, value in forecast_data[-5:]) / min(5, len(forecast_data))
                        weight = max(1, int(avg_forecast))
                        for _ in range(min(weight, 5)):  # Cap forecast weight
                            workload_queries.append(matching_analysis.query_template)

            logger.info(f"Cost-based optimization with {len(workload_queries)} queries")

            # Collect schema information from cost-based advisor
            schema_info = await self._collect_cost_based_schema_info()

            # Run cost-based optimization
            cost_result = self.cost_based_advisor.recommend_indexes(
                workload_queries=workload_queries,
                schema_info=schema_info,
                time_limit_seconds=self.config.cost_based_time_limit_seconds,
                max_indexes_per_table=self.config.cost_based_max_indexes_per_table
            )

            # Convert cost-based results to recommendation format
            recommendations = []
            for index in cost_result.best_solution.indexes:
                # Create a recommendation-like result
                recommendation = self._create_recommendation_from_cost_result(
                    index, cost_result, query_analyses
                )
                recommendations.append(recommendation)

            logger.info(f"Generated {len(recommendations)} cost-based index recommendations")
            logger.info(f"Optimization completed in {cost_result.optimization_time:.2f} seconds")
            logger.info(f"Final benefit score: {cost_result.best_solution.benefit_score:.2f}")

            return recommendations

        except Exception as e:
            logger.error(f"Failed to generate cost-based recommendations: {e}")
            # Fallback to pattern-based approach
            return await self.recommendation_engine.generate_recommendations(query_analyses, forecasts)

    async def _collect_cost_based_schema_info(self) -> Dict[str, Dict]:
        """Collect enhanced schema information for cost-based optimization"""
        try:
            # Build enhanced schema info for cost-based advisor
            schema_info = {}

            # Try to get table statistics from the database
            if hasattr(self.cost_based_advisor, 'connection'):
                query = """
                SELECT
                    schemaname, tablename,
                    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
                    pg_total_relation_size(schemaname||'.'||tablename) as size_bytes
                FROM pg_tables
                WHERE schemaname NOT IN ('information_schema', 'pg_catalog', 'hypopg')
                LIMIT 10
                """

                try:
                    result = self.cost_based_advisor.connection.execute(query)
                    for row in result.fetchall():
                        table_name = row[1]
                        size_mb = row[3] / (1024 * 1024) if row[3] else 1.0

                        # Get column information
                        col_query = """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = %s
                        ORDER BY ordinal_position
                        """
                        col_result = self.cost_based_advisor.connection.execute(col_query, (table_name,))
                        columns = [col_row[0] for col_row in col_result.fetchall()]

                        schema_info[table_name] = {
                            'columns': columns,
                            'size_mb': size_mb
                        }

                except Exception as schema_error:
                    logger.warning(f"Could not collect detailed schema: {schema_error}")

            # Fallback to basic schema if database collection fails
            if not schema_info:
                schema_info = {
                    'movie_info': {'columns': ['movie_id', 'info_type_id', 'info'], 'size_mb': 500},
                    'cast_info': {'columns': ['person_id', 'movie_id', 'role_id', 'note'], 'size_mb': 800},
                    'title': {'columns': ['title_id', 'title', 'kind_id', 'production_year'], 'size_mb': 600},
                    'person_name': {'columns': ['person_id', 'name', 'gender'], 'size_mb': 100},
                    'movie_keyword': {'columns': ['movie_id', 'keyword_id'], 'size_mb': 300}
                }

            return schema_info

        except Exception as e:
            logger.warning(f"Failed to collect enhanced schema info: {e}")
            return {}

    def _create_recommendation_from_cost_result(self, index, cost_result, query_analyses):
        """Convert cost-based index result to RecommendationResult format"""
        from index_recommendation_engine import RecommendationResult, RecommendationType

        # Find queries that would benefit from this index
        benefited_queries = []
        for analysis in query_analyses:
            if index.table_name.lower() in analysis.query_template.lower():
                benefited_queries.append(analysis.template_id)

        # Estimate recommendation strength based on benefit score
        benefit_score = cost_result.best_solution.benefit_score
        if benefit_score > 100:
            strength = "STRONG"
        elif benefit_score > 50:
            strength = "MODERATE"
        else:
            strength = "MILD"

        # Create recommendation result
        recommendation = RecommendationResult(
            index_type=RecommendationType.SINGLE_COLUMN if len(index.columns) == 1 else RecommendationType.COMPOSITE,
            table_name=index.table_name,
            columns=index.columns,
            index_name=f"idx_{index.table_name}_{'_'.join(index.columns)}",
            recommendation_strength=strength,
            estimated_impact_queries=len(benefited_queries),
            estimated_cost_savings=benefit_score * 10,  # Rough conversion
            creation_cost=index.creation_cost,
            maintenance_cost=float(cost_result.best_solution.maintenance_cost),
            space_requirement=float(index.estimated_size_mb),
            benefit_queries=benefited_queries,
            forecast_impact=[],
            risk_assessment="LOW",  # Cost-based analysis considers actual risks
            confidence_score=min(0.95, 0.5 + (benefit_score / 200)),  # Scale confidence with benefit
            justification=f"Cost-based optimization using PostgreSQL query planner. "
                         f"Estimated benefit score: {benefit_score:.2f}. "
                         f"Index type: {index.index_type.value}."
        )

        return recommendation

    async def _create_comprehensive_result(self,
                                         start_time: datetime,
                                         end_time: datetime,
                                         query_analyses: List[QueryAnalysis],
                                         forecasts: Dict[str, List[Tuple[datetime, float]]],
                                         recommendations: List[RecommendationResult],
                                         workload_data: List[WorkloadMetrics]) -> ForecastIndexResult:
        """Create comprehensive analysis result"""
        # Calculate summary statistics
        total_hours = (end_time - start_time).total_seconds() / 3600

        # Count high-confidence recommendations
        high_confidence_count = sum(
            1 for rec in recommendations if rec.recommendation_strength in ["STRONG", "MODERATE"]
        )

        # Calculate estimated impact
        total_impact_queries = sum(rec.candidate.estimated_impact_queries for rec in recommendations)
        estimated_cost_savings = sum(
            rec.candidate.estimated_cost_reduction for rec in recommendations
        )

        # Create forecast summary
        forecast_summary = {
            "total_forecasts": len(forecasts),
            "forecast_horizon_hours": self.config.forecast_horizon_hours,
            "total_forecasted_queries": sum(
                sum(val for _, val in forecast) for forecast in forecasts.values()
            ),
            "confidence_distribution": self._calculate_confidence_distribution(forecasts)
        }

        # Create workload summary
        workload_summary = {
            "total_metrics": len(workload_data),
            "total_queries": sum(
                getattr(m, 'execution_count', 0) for m in workload_data
            ),
            "unique_templates": len(set(
                getattr(m, 'template_hash', '') for m in workload_data if hasattr(m, 'template_hash')
            )),
            "analysis_period_hours": total_hours
        }

        return ForecastIndexResult(
            timestamp=datetime.now(),
            analysis_period_hours=int(total_hours),
            forecast_horizon_hours=self.config.forecast_horizon_hours,
            total_templates_analyzed=len(query_analyses),
            total_recommendations_generated=len(recommendations),
            high_confidence_recommendations=high_confidence_count,
            estimated_total_impact_queries=total_impact_queries,
            estimated_cost_savings=estimated_cost_savings,
            recommendations=recommendations,
            forecast_summary=forecast_summary,
            workload_summary=workload_summary
        )

    def _calculate_confidence_distribution(self, forecasts: Dict[str, List[Tuple[datetime, float]]]) -> Dict[str, int]:
        """Calculate confidence distribution for forecasts"""
        # Simplified confidence calculation
        # In a real implementation, this would use actual confidence scores from forecasts
        return {
            "high": len(forecasts),
            "medium": 0,
            "low": 0
        }

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached result is still valid"""
        if cache_key not in self.result_cache:
            return False

        result = self.result_cache[cache_key]
        age = datetime.now() - result.timestamp
        return age < timedelta(hours=self.config.cache_ttl_hours)

    async def _start_scheduled_mode(self):
        """Start scheduled recommendation generation"""
        async def scheduled_task():
            while True:
                try:
                    await self.generate_recommendations()
                    await asyncio.sleep(self.config.schedule_interval_minutes * 60)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in scheduled task: {e}")
                    await asyncio.sleep(60)  # Wait 1 minute before retrying

        self.scheduled_task = asyncio.create_task(scheduled_task())
        logger.info(f"Started scheduled mode with {self.config.schedule_interval_minutes} minute interval")

    async def _start_streaming_mode(self):
        """Start streaming real-time recommendations"""
        # This would implement real-time monitoring and recommendation generation
        # For now, we'll use a simplified version with periodic updates
        logger.info("Streaming mode not fully implemented - using simplified periodic updates")
        await self._start_scheduled_mode()

    async def get_service_status(self) -> Dict[str, Any]:
        """Get current service status"""
        status = {
            "initialized": self.is_initialized,
            "running": self.is_running,
            "mode": self.config.mode.value,
            "last_analysis_time": self.last_analysis_time.isoformat() if self.last_analysis_time else None,
            "cache_size": len(self.result_cache),
            "config": {
                "forecast_horizon_hours": self.config.forecast_horizon_hours,
                "recommendation_strategy": self.config.recommendation_strategy.value,
                "max_recommendations": self.config.max_recommendations,
                "enable_hypothetical_analysis": self.config.enable_hypothetical_analysis
            }
        }

        return status

    async def clear_cache(self):
        """Clear the recommendation cache"""
        self.result_cache.clear()
        logger.info("Recommendation cache cleared")

    async def get_recommendation_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get history of recent recommendations"""
        # Sort cache entries by timestamp
        sorted_results = sorted(
            self.result_cache.values(),
            key=lambda x: x.timestamp,
            reverse=True
        )

        history = []
        for result in sorted_results[:limit]:
            history.append({
                "timestamp": result.timestamp.isoformat(),
                "total_recommendations": result.total_recommendations_generated,
                "high_confidence_recommendations": result.high_confidence_recommendations,
                "estimated_impact_queries": result.estimated_total_impact_queries,
                "estimated_cost_savings": result.estimated_cost_savings
            })

        return history

    def export_latest_results(self, format: str = "json") -> str:
        """Export the latest analysis results"""
        if not self.result_cache:
            return "No results available"

        # Get the most recent result
        latest_result = max(self.result_cache.values(), key=lambda x: x.timestamp)

        # Export recommendations using the recommendation engine
        return self.recommendation_engine.export_recommendations(
            latest_result.recommendations, format
        )