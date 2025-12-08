"""
Index Recommendation Engine for NeurDB
Combines pattern-based analysis with hypothetical index validation
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

from index_advisor import WorkloadIndexAdvisor, IndexCandidate, QueryAnalysis, IndexScenario
from hypothetical_analyzer import HypotheticalIndexAnalyzer, HypotheticalIndexAnalysis, HypotheticalIndexConfig

logger = logging.getLogger(__name__)


class RecommendationStrategy(Enum):
    """Recommendation generation strategies"""
    CONSERVATIVE = "conservative"  # Only recommend indexes with proven benefits
    BALANCED = "balanced"  # Mix of proven and theoretical benefits
    AGGRESSIVE = "aggressive"  # Include more speculative recommendations
    FORECAST_DRIVEN = "forecast_driven"  # Prioritize based on workload forecasts


@dataclass
class RecommendationConfig:
    """Configuration for recommendation engine"""
    # Index advisor configuration
    min_benefit_threshold: float = 0.1
    max_indexes_per_table: int = 5
    index_size_penalty: float = 0.1
    maintenance_overhead_factor: float = 0.05
    forecast_horizon_hours: int = 24
    max_index_columns: int = 4

    # Hypothetical analysis configuration
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "neurdb"
    database_user: str = "postgres"
    database_password: str = "postgres"
    enable_hypothetical_analysis: bool = True
    min_hypothetical_cost_reduction: float = 0.05

    # Recommendation strategy
    strategy: RecommendationStrategy = RecommendationStrategy.BALANCED
    max_recommendations: int = 50
    include_forecast_impact: bool = True
    require_hypothetical_validation: bool = True

    # Advanced options
    enable_explain_analyze: bool = False  # Use ANALYZE for more accurate results
    cache_results: bool = True
    cache_ttl_hours: int = 24


@dataclass
class RecommendationResult:
    """Complete recommendation result with all analysis data"""
    candidate: IndexCandidate
    hypothetical_analysis: Optional[HypotheticalIndexAnalysis]
    combined_score: float
    recommendation_strength: str  # "STRONG", "MODERATE", "WEAK"
    implementation_priority: int  # 1 = highest priority
    risk_level: str  # "LOW", "MEDIUM", "HIGH"
    estimated_roi: float  # Return on investment estimate
    reasoning: List[str]
    warnings: List[str]


class IndexRecommendationEngine:
    """
    Advanced index recommendation engine that combines workload pattern analysis
    with hypothetical index validation to provide high-quality recommendations
    """

    def __init__(self, config: RecommendationConfig):
        """
        Initialize the recommendation engine

        Args:
            config: Configuration parameters
        """
        self.config = config

        # Initialize components
        advisor_config = {
            'min_benefit_threshold': config.min_benefit_threshold,
            'max_indexes_per_table': config.max_indexes_per_table,
            'index_size_penalty': config.index_size_penalty,
            'maintenance_overhead_factor': config.maintenance_overhead_factor,
            'forecast_horizon_hours': config.forecast_horizon_hours,
            'enable_hypothetical_analysis': config.enable_hypothetical_analysis,
            'max_index_columns': config.max_index_columns
        }

        self.index_advisor = WorkloadIndexAdvisor(advisor_config)

        hypothetical_config = HypotheticalIndexConfig(
            database_host=config.database_host,
            database_port=config.database_port,
            database_name=config.database_name,
            database_user=config.database_user,
            database_password=config.database_password,
            enable_explain_analyze=config.enable_explain_analyze,
            min_cost_reduction_threshold=config.min_hypothetical_cost_reduction,
            cache_results=config.cache_results,
            cache_ttl_hours=config.cache_ttl_hours
        )

        self.hypothetical_analyzer = HypotheticalIndexAnalyzer(hypothetical_config)

        # Internal state
        self.is_initialized = False
        self.schema_info = {}
        self.recommendation_cache = {}

        logger.info("IndexRecommendationEngine initialized")

    async def initialize(self, schema_info: Optional[Dict[str, Dict]] = None):
        """
        Initialize the recommendation engine

        Args:
            schema_info: Optional database schema information
        """
        try:
            # Set schema information if provided
            if schema_info:
                self.index_advisor.set_schema_info(schema_info)
                self.schema_info = schema_info

            # Initialize hypothetical analyzer
            if self.config.enable_hypothetical_analysis:
                await self.hypothetical_analyzer.initialize()

            self.is_initialized = True
            logger.info("IndexRecommendationEngine initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize IndexRecommendationEngine: {e}")
            raise

    async def generate_recommendations(self,
                                     query_analyses: List[QueryAnalysis],
                                     forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]] = None,
                                     sample_queries: Optional[Dict[str, List[str]]] = None) -> List[RecommendationResult]:
        """
        Generate comprehensive index recommendations

        Args:
            query_analyses: List of query analyses
            forecasts: Optional forecast data
            sample_queries: Optional sample queries for validation

        Returns:
            List of ranked recommendation results
        """
        if not self.is_initialized:
            raise RuntimeError("Engine not initialized. Call initialize() first.")

        logger.info(f"Generating recommendations from {len(query_analyses)} query analyses")

        # Generate pattern-based candidates
        pattern_candidates = self.index_advisor.generate_index_candidates(query_analyses, forecasts)

        logger.info(f"Generated {len(pattern_candidates)} pattern-based candidates")

        # Validate with hypothetical analysis if enabled
        if self.config.enable_hypothetical_analysis and pattern_candidates:
            hypothetical_results = await self._validate_with_hypothetical_analysis(
                pattern_candidates, query_analyses, sample_queries
            )
        else:
            hypothetical_results = {}

        # Create comprehensive recommendation results
        recommendations = self._create_recommendation_results(
            pattern_candidates, hypothetical_results, forecasts, query_analyses
        )

        # Apply strategy-specific filtering and ranking
        recommendations = self._apply_strategy_filtering(recommendations, forecasts)

        # Apply final ranking
        recommendations = self._final_ranking(recommendations)

        logger.info(f"Generated {len(recommendations)} final recommendations")

        return recommendations[:self.config.max_recommendations]

    async def _validate_with_hypothetical_analysis(self,
                                                 candidates: List[IndexCandidate],
                                                 query_analyses: List[QueryAnalysis],
                                                 sample_queries: Optional[Dict[str, List[str]]]) -> Dict[str, HypotheticalIndexAnalysis]:
        """Validate candidates using hypothetical index analysis"""
        try:
            logger.info(f"Starting hypothetical validation for {len(candidates)} candidates")

            # Get validated recommendations with actual performance measurements
            validated_analyses = await self.hypothetical_analyzer.get_index_recommendations_with_analysis(
                candidates, query_analyses, sample_queries
            )

            # Create mapping from candidate to analysis
            analysis_map = {}
            for analysis in validated_analyses:
                candidate_key = self._get_candidate_key(analysis.index_candidate)
                analysis_map[candidate_key] = analysis

            logger.info(f"Completed hypothetical validation for {len(analysis_map)} candidates")
            return analysis_map

        except Exception as e:
            logger.error(f"Hypothetical analysis failed: {e}")
            # Return empty map to continue with pattern-based recommendations
            return {}

    def _create_recommendation_results(self,
                                     candidates: List[IndexCandidate],
                                     hypothetical_results: Dict[str, HypotheticalIndexAnalysis],
                                     forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]],
                                     query_analyses: List[QueryAnalysis]) -> List[RecommendationResult]:
        """Create comprehensive recommendation results"""
        recommendations = []

        for candidate in candidates:
            candidate_key = self._get_candidate_key(candidate)
            hypothetical_analysis = hypothetical_results.get(candidate_key)

            # Calculate combined score
            combined_score = self._calculate_combined_score(candidate, hypothetical_analysis, forecasts)

            # Determine recommendation strength
            strength = self._determine_recommendation_strength(combined_score, hypothetical_analysis)

            # Calculate implementation priority
            priority = self._calculate_implementation_priority(candidate, hypothetical_analysis, combined_score)

            # Assess risk level
            risk_level = self._assess_risk_level(candidate, hypothetical_analysis)

            # Estimate ROI
            roi = self._estimate_roi(candidate, hypothetical_analysis)

            # Generate reasoning
            reasoning = self._generate_reasoning(candidate, hypothetical_analysis, forecasts)

            # Generate warnings
            warnings = self._generate_warnings(candidate, hypothetical_analysis)

            recommendation = RecommendationResult(
                candidate=candidate,
                hypothetical_analysis=hypothetical_analysis,
                combined_score=combined_score,
                recommendation_strength=strength,
                implementation_priority=priority,
                risk_level=risk_level,
                estimated_roi=roi,
                reasoning=reasoning,
                warnings=warnings
            )

            recommendations.append(recommendation)

        return recommendations

    def _calculate_combined_score(self, candidate: IndexCandidate,
                                hypothetical_analysis: Optional[HypotheticalIndexAnalysis],
                                forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> float:
        """Calculate combined score from pattern analysis and hypothetical validation"""

        # Base score from pattern analysis
        base_score = candidate.estimated_benefit

        # Boost from hypothetical analysis
        if hypothetical_analysis and hypothetical_analysis.analysis_status.value == "success":
            if hypothetical_analysis.index_usage_detected:
                # Weight actual measured improvement more heavily
                actual_improvement = hypothetical_analysis.cost_reduction_percentage / 100.0
                hypothetical_boost = min(0.5, actual_improvement * 2)  # Cap boost at 0.5
                base_score = (base_score + hypothetical_boost) / 2  # Average the two
            else:
                # Penalty if index wasn't used despite being created
                base_score *= 0.7

        # Forecast impact
        if forecasts and self.config.include_forecast_impact:
            template_hash = candidate.supporting_templates[0] if candidate.supporting_templates else None
            if template_hash and template_hash in forecasts:
                forecast_data = forecasts[template_hash]
                future_queries = sum(val for _, val in forecast_data)
                forecast_factor = min(1.0, future_queries / 1000.0)  # Normalize to [0, 1]
                base_score = base_score * (0.7 + 0.3 * forecast_factor)  # 30% weight for forecast

        # Strategy-specific adjustments
        if self.config.strategy == RecommendationStrategy.CONSERVATIVE:
            if not hypothetical_analysis or hypothetical_analysis.analysis_status.value != "success":
                base_score *= 0.5  # Heavy penalty for unvalidated recommendations

        elif self.config.strategy == RecommendationStrategy.AGGRESSIVE:
            # Boost for novel candidates
            if candidate.scenario in [IndexScenario.ORDER_BY, IndexScenario.JOIN_OPTIMIZATION]:
                base_score *= 1.2

        elif self.config.strategy == RecommendationStrategy.FORECAST_DRIVEN:
            if forecasts and self.config.include_forecast_impact:
                # Even heavier weight for forecasts
                template_hash = candidate.supporting_templates[0] if candidate.supporting_templates else None
                if template_hash and template_hash in forecasts:
                    forecast_data = forecasts[template_hash]
                    future_queries = sum(val for _, val in forecast_data)
                    forecast_factor = min(1.0, future_queries / 500.0)
                    base_score = base_score * (0.4 + 0.6 * forecast_factor)

        return min(1.0, base_score)

    def _determine_recommendation_strength(self, combined_score: float,
                                         hypothetical_analysis: Optional[HypotheticalIndexAnalysis]) -> str:
        """Determine recommendation strength based on evidence"""
        if hypothetical_analysis and hypothetical_analysis.analysis_status.value == "success":
            if hypothetical_analysis.index_usage_detected:
                if combined_score >= 0.8:
                    return "STRONG"
                elif combined_score >= 0.5:
                    return "MODERATE"
                else:
                    return "WEAK"
            else:
                return "WEAK"  # Index wasn't used despite validation
        else:
            # No hypothetical validation
            if combined_score >= 0.9:
                return "STRONG"
            elif combined_score >= 0.6:
                return "MODERATE"
            else:
                return "WEAK"

    def _calculate_implementation_priority(self, candidate: IndexCandidate,
                                          hypothetical_analysis: Optional[HypotheticalIndexAnalysis],
                                          combined_score: float) -> int:
        """Calculate implementation priority (1 = highest)"""
        priority_score = combined_score * 100

        # Boost for high-frequency scenarios
        if candidate.scenario == IndexScenario.HIGH_FREQUENCY:
            priority_score += 20

        # Boost for strong hypothetical validation
        if (hypothetical_analysis and
            hypothetical_analysis.index_usage_detected and
            hypothetical_analysis.cost_reduction_percentage > 20):
            priority_score += 30

        # Boost for low-risk candidates
        if candidate.estimated_size_mb < 100:
            priority_score += 10

        # Penalty for large indexes
        if candidate.estimated_size_mb > 500:
            priority_score -= 20

        # Convert to priority ranking (lower number = higher priority)
        return max(1, int(100 - priority_score))

    def _assess_risk_level(self, candidate: IndexCandidate,
                         hypothetical_analysis: Optional[HypotheticalIndexAnalysis]) -> str:
        """Assess implementation risk level"""
        risk_factors = 0

        # Size-based risk
        if candidate.estimated_size_mb > 1000:
            risk_factors += 2
        elif candidate.estimated_size_mb > 500:
            risk_factors += 1

        # Complexity-based risk
        if len(candidate.columns) > 3:
            risk_factors += 1

        # Validation-based risk
        if not hypothetical_analysis or hypothetical_analysis.analysis_status.value != "success":
            risk_factors += 2
        elif not hypothetical_analysis.index_usage_detected:
            risk_factors += 3  # High risk if index wasn't used

        # Scenario-based risk
        if candidate.scenario in [IndexScenario.JOIN_OPTIMIZATION, IndexScenario.AGGREGATION]:
            risk_factors += 1

        # Determine risk level
        if risk_factors >= 4:
            return "HIGH"
        elif risk_factors >= 2:
            return "MEDIUM"
        else:
            return "LOW"

    def _estimate_roi(self, candidate: IndexCandidate,
                     hypothetical_analysis: Optional[HypotheticalIndexAnalysis]) -> float:
        """Estimate return on investment for the index"""
        # Cost factors
        creation_cost = candidate.creation_cost
        maintenance_cost = candidate.maintenance_overhead * 12  # Annual maintenance

        # Benefit factors
        if hypothetical_analysis and hypothetical_analysis.analysis_status.value == "success":
            # Use actual measured improvements
            time_saved_per_query = hypothetical_analysis.time_reduction_percentage / 100.0
            cost_saved_per_query = hypothetical_analysis.cost_reduction_percentage / 100.0
        else:
            # Use estimated benefits
            time_saved_per_query = candidate.estimated_benefit * 0.5
            cost_saved_per_query = candidate.estimated_benefit

        # Total annual benefit
        queries_per_year = candidate.estimated_impact_queries * 365
        annual_benefit = queries_per_year * (time_saved_per_query + cost_saved_per_query) * 0.01  # Monetary factor

        # ROI calculation
        total_cost = creation_cost + maintenance_cost
        if total_cost > 0:
            roi = (annual_benefit - total_cost) / total_cost
            return max(-1.0, roi)  # Cap at -100% loss
        else:
            return annual_benefit  # Pure benefit if no cost

    def _generate_reasoning(self, candidate: IndexCandidate,
                           hypothetical_analysis: Optional[HypotheticalIndexAnalysis],
                           forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[str]:
        """Generate reasoning for the recommendation"""
        reasoning = []

        # Base scenario reasoning
        if candidate.scenario == IndexScenario.HIGH_FREQUENCY:
            reasoning.append(f"High-impact query executed {candidate.estimated_impact_queries} times")
        elif candidate.scenario == IndexScenario.SELECTIVITY_LOW:
            reasoning.append("Will significantly reduce table scan costs for selective queries")
        elif candidate.scenario == IndexScenario.ORDER_BY:
            reasoning.append("Will eliminate sorting costs for ORDER BY operations")
        elif candidate.scenario == IndexScenario.JOIN_OPTIMIZATION:
            reasoning.append("Will improve join performance by indexing join columns")
        elif candidate.scenario == IndexScenario.AGGREGATION:
            reasoning.append("Will accelerate GROUP BY and aggregation operations")

        # Hypothetical validation reasoning
        if hypothetical_analysis and hypothetical_analysis.analysis_status.value == "success":
            if hypothetical_analysis.index_usage_detected:
                reasoning.append(
                    f"Hypothetical analysis shows {hypothetical_analysis.cost_reduction_percentage:.1f}% cost reduction"
                )
                reasoning.append(
                    f"Query execution time reduced by {hypothetical_analysis.time_reduction_percentage:.1f}%"
                )
                if hypothetical_analysis.plan_changes:
                    reasoning.append(f"Plan optimization: {', '.join(hypothetical_analysis.plan_changes)}")
            else:
                reasoning.append("Note: Hypothetical index was not used in execution plan")

        # Forecast reasoning
        if forecasts and self.config.include_forecast_impact:
            template_hash = candidate.supporting_templates[0] if candidate.supporting_templates else None
            if template_hash and template_hash in forecasts:
                future_queries = sum(val for _, val in forecasts[template_hash])
                reasoning.append(f"Forecasted {future_queries:.0f} additional executions in next 24h")

        return reasoning

    def _generate_warnings(self, candidate: IndexCandidate,
                         hypothetical_analysis: Optional[HypotheticalIndexAnalysis]) -> List[str]:
        """Generate implementation warnings"""
        warnings = []

        # Size warnings
        if candidate.estimated_size_mb > 1000:
            warnings.append(f"Large index ({candidate.estimated_size_mb:.1f} MB) - consider storage impact")
        elif candidate.estimated_size_mb > 500:
            warnings.append(f"Medium-sized index ({candidate.estimated_size_mb:.1f} MB)")

        # Complexity warnings
        if len(candidate.columns) > 3:
            warnings.append(f"Complex {len(candidate.columns)}-column index may have higher maintenance overhead")

        # Validation warnings
        if hypothetical_analysis:
            if hypothetical_analysis.analysis_status.value != "success":
                warnings.append(f"Hypothetical analysis failed: {hypothetical_analysis.error_message}")
            elif not hypothetical_analysis.index_usage_detected:
                warnings.append("Index was not used in test execution - may not provide expected benefits")

        # Scenario-specific warnings
        if candidate.scenario == IndexScenario.JOIN_OPTIMIZATION:
            warnings.append("Join optimization indexes should be tested with actual join queries")

        # Confidence warnings
        if candidate.confidence_score < 0.5:
            warnings.append("Low confidence score - benefits may vary significantly")

        return warnings

    def _apply_strategy_filtering(self, recommendations: List[RecommendationResult],
                                forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[RecommendationResult]:
        """Apply strategy-specific filtering"""
        filtered = []

        for rec in recommendations:
            include = True

            # Conservative strategy: only include validated recommendations
            if self.config.strategy == RecommendationStrategy.CONSERVATIVE:
                if (self.config.require_hypothetical_validation and
                    (not rec.hypothetical_analysis or
                     rec.hypothetical_analysis.analysis_status.value != "success" or
                     not rec.hypothetical_analysis.index_usage_detected)):
                    include = False

            # Forecast-driven strategy: prioritize based on forecast impact
            elif self.config.strategy == RecommendationStrategy.FORECAST_DRIVEN:
                if not forecasts and self.config.include_forecast_impact:
                    include = False

            # Quality threshold
            if include and rec.combined_score < 0.1:
                include = False

            if include:
                filtered.append(rec)

        return filtered

    def _final_ranking(self, recommendations: List[RecommendationResult]) -> List[RecommendationResult]:
        """Apply final ranking to recommendations"""
        # Primary sort: combined score (descending)
        # Secondary sort: implementation priority (ascending)
        # Tertiary sort: estimated ROI (descending)

        return sorted(
            recommendations,
            key=lambda x: (-x.combined_score, x.implementation_priority, -x.estimated_roi)
        )

    def _get_candidate_key(self, candidate: IndexCandidate) -> str:
        """Generate unique key for candidate"""
        return f"{candidate.table_name}_{'_'.join(sorted(candidate.columns))}_{candidate.index_type.value}"

    def export_recommendations(self, recommendations: List[RecommendationResult],
                             format: str = "json") -> str:
        """Export recommendations in specified format"""
        if format.lower() == "json":
            return self._export_json(recommendations)
        elif format.lower() == "sql":
            return self._export_sql(recommendations)
        elif format.lower() == "markdown":
            return self._export_markdown(recommendations)
        else:
            raise ValueError(f"Unsupported export format: {format}")

    def _export_json(self, recommendations: List[RecommendationResult]) -> str:
        """Export recommendations as JSON"""
        export_data = {
            "timestamp": datetime.now().isoformat(),
            "strategy": self.config.strategy.value,
            "total_recommendations": len(recommendations),
            "recommendations": []
        }

        for rec in recommendations:
            rec_data = {
                "rank": rec.implementation_priority,
                "table": rec.candidate.table_name,
                "columns": rec.candidate.columns,
                "index_type": rec.candidate.index_type.value,
                "scenario": rec.candidate.scenario.value,
                "strength": rec.recommendation_strength,
                "combined_score": rec.combined_score,
                "estimated_benefit": rec.candidate.estimated_benefit,
                "confidence_score": rec.candidate.confidence_score,
                "estimated_size_mb": rec.candidate.estimated_size_mb,
                "creation_cost": rec.candidate.creation_cost,
                "maintenance_overhead": rec.candidate.maintenance_overhead,
                "estimated_impact_queries": rec.candidate.estimated_impact_queries,
                "risk_level": rec.risk_level,
                "estimated_roi": rec.estimated_roi,
                "sql_statements": rec.candidate.sql_statements,
                "reasoning": rec.reasoning,
                "warnings": rec.warnings
            }

            if rec.hypothetical_analysis:
                rec_data["hypothetical_analysis"] = {
                    "cost_reduction_percentage": rec.hypothetical_analysis.cost_reduction_percentage,
                    "time_reduction_percentage": rec.hypothetical_analysis.time_reduction_percentage,
                    "plan_changes": rec.hypothetical_analysis.plan_changes,
                    "index_usage_detected": rec.hypothetical_analysis.index_usage_detected
                }

            export_data["recommendations"].append(rec_data)

        return json.dumps(export_data, indent=2)

    def _export_sql(self, recommendations: List[RecommendationResult]) -> str:
        """Export recommendations as SQL script"""
        sql_statements = [
            "-- Index Recommendations",
            f"-- Generated: {datetime.now().isoformat()}",
            f"-- Strategy: {self.config.strategy.value}",
            f"-- Total recommendations: {len(recommendations)}",
            ""
        ]

        for i, rec in enumerate(recommendations, 1):
            sql_statements.extend([
                f"-- Recommendation {i}: {rec.recommendation_strength} priority",
                f"-- Table: {rec.candidate.table_name}",
                f"-- Columns: {', '.join(rec.candidate.columns)}",
                f"-- Estimated benefit: {rec.combined_score:.2f}",
                f"-- Risk level: {rec.risk_level}",
            ])

            if rec.reasoning:
                sql_statements.append("-- Reasoning:")
                for reason in rec.reasoning:
                    sql_statements.append(f"--   - {reason}")

            if rec.warnings:
                sql_statements.append("-- Warnings:")
                for warning in rec.warnings:
                    sql_statements.append(f"--   - {warning}")

            sql_statements.extend(rec.candidate.sql_statements)
            sql_statements.append("")

        return "\n".join(sql_statements)

    def _export_markdown(self, recommendations: List[RecommendationResult]) -> str:
        """Export recommendations as Markdown report"""
        lines = [
            "# Index Recommendations Report",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Strategy:** {self.config.strategy.value}",
            f"**Total Recommendations:** {len(recommendations)}",
            ""
        ]

        # Summary table
        lines.extend([
            "## Summary",
            "",
            "| Priority | Table | Columns | Type | Strength | Score | Risk | ROI |",
            "|----------|-------|---------|------|----------|-------|------|-----|",
        ])

        for rec in recommendations:
            lines.append(
                f"| {rec.implementation_priority} | {rec.candidate.table_name} | "
                f"{', '.join(rec.candidate.columns)} | {rec.candidate.index_type.value} | "
                f"{rec.recommendation_strength} | {rec.combined_score:.2f} | {rec.risk_level} | "
                f"{rec.estimated_roi:.1%} |"
            )

        lines.extend(["", "## Detailed Recommendations", ""])

        for i, rec in enumerate(recommendations, 1):
            lines.extend([
                f"### {i}. {rec.candidate.table_name}({', '.join(rec.candidate.columns)})",
                "",
                f"- **Type:** {rec.candidate.index_type.value}",
                f"- **Scenario:** {rec.candidate.scenario.value}",
                f"- **Strength:** {rec.recommendation_strength}",
                f"- **Score:** {rec.combined_score:.2f}",
                f"- **Risk Level:** {rec.risk_level}",
                f"- **Estimated ROI:** {rec.estimated_roi:.1%}",
                f"- **Estimated Size:** {rec.candidate.estimated_size_mb:.1f} MB",
                f"- **Impact Queries:** {rec.candidate.estimated_impact_queries}",
                ""
            ])

            if rec.reasoning:
                lines.extend(["**Reasoning:**", ""])
                for reason in rec.reasoning:
                    lines.append(f"- {reason}")
                lines.append("")

            if rec.warnings:
                lines.extend(["**Warnings:**", ""])
                for warning in rec.warnings:
                    lines.append(f"- ⚠️ {warning}")
                lines.append("")

            if rec.candidate.sql_statements:
                lines.extend(["**SQL:**", ""])
                lines.extend([f"```sql\n{stmt}\n```" for stmt in rec.candidate.sql_statements])
                lines.append("")

            if rec.hypothetical_analysis:
                lines.extend([
                    "**Hypothetical Analysis Results:**",
                    "",
                    f"- Cost Reduction: {rec.hypothetical_analysis.cost_reduction_percentage:.1f}%",
                    f"- Time Reduction: {rec.hypothetical_analysis.time_reduction_percentage:.1f}%",
                    f"- Index Used: {rec.hypothetical_analysis.index_usage_detected}",
                    ""
                ])

                if rec.hypothetical_analysis.plan_changes:
                    lines.extend(["Plan Changes:", ""])
                    for change in rec.hypothetical_analysis.plan_changes:
                        lines.append(f"- {change}")
                    lines.append("")

            lines.append("---")

        return "\n".join(lines)

    async def cleanup(self):
        """Clean up resources"""
        try:
            if self.config.enable_hypothetical_analysis:
                await self.hypothetical_analyzer.cleanup()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

        logger.info("IndexRecommendationEngine cleanup complete")