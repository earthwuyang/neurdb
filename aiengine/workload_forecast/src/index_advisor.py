"""
Index Advisor for NeurDB Workload Forecasting
Generates index recommendations based on workload patterns and forecasting
"""

import re
import json
import logging
from typing import Dict, List, Tuple, Set, Optional, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)


class IndexType(Enum):
    """Supported index types"""
    BTREE = "btree"
    HASH = "hash"
    GIN = "gin"
    GIST = "gist"
    SPGIST = "spgist"
    BRIN = "brin"


class IndexScenario(Enum):
    """Index recommendation scenarios"""
    HIGH_FREQUENCY = "high_frequency"
    SELECTIVITY_LOW = "selectivity_low"
    ORDER_BY = "order_by"
    JOIN_OPTIMIZATION = "join_optimization"
    AGGREGATION = "aggregation"
    UNIQUE_CONSTRAINT = "unique_constraint"
    FULLTEXT_SEARCH = "fulltext_search"


@dataclass
class IndexCandidate:
    """Represents a potential index recommendation"""
    table_name: str
    columns: List[str]
    index_type: IndexType
    scenario: IndexScenario
    estimated_benefit: float
    confidence_score: float
    estimated_size_mb: float
    creation_cost: float
    maintenance_overhead: float
    sql_statements: List[str]
    supporting_templates: List[str]
    supporting_queries: List[str]
    estimated_impact_queries: int
    estimated_cost_reduction: float


@dataclass
class QueryAnalysis:
    """Analysis results for a query template"""
    template_hash: str
    template_text: str
    tables_used: List[str]
    columns_accessed: Dict[str, List[str]]
    join_conditions: List[str]
    where_conditions: List[str]
    order_by_columns: List[str]
    group_by_columns: List[str]
    aggregate_functions: List[str]
    selectivity_estimates: Dict[str, float]
    execution_frequency: int
    avg_execution_time_ms: float
    join_cardinality: Dict[str, int]


class WorkloadIndexAdvisor:
    """
    Advanced index advisor that analyzes workload patterns and generates recommendations
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize index advisor

        Args:
            config: Configuration parameters
                - min_benefit_threshold: Minimum benefit score for recommendations (default: 0.1)
                - max_indexes_per_table: Maximum indexes per table (default: 5)
                - index_size_penalty: Penalty for large indexes (default: 0.1)
                - maintenance_overhead_factor: Factor for maintenance cost (default: 0.05)
                - forecast_horizon_hours: Hours to forecast for benefit calculation (default: 24)
                - enable_hypothetical_analysis: Whether to use EXPLAIN analysis (default: True)
                - max_index_columns: Maximum columns in composite index (default: 4)
        """
        self.config = config or {}
        self.min_benefit_threshold = self.config.get('min_benefit_threshold', 0.1)
        self.max_indexes_per_table = self.config.get('max_indexes_per_table', 5)
        self.index_size_penalty = self.config.get('index_size_penalty', 0.1)
        self.maintenance_overhead_factor = self.config.get('maintenance_overhead_factor', 0.05)
        self.forecast_horizon_hours = self.config.get('forecast_horizon_hours', 24)
        self.enable_hypothetical_analysis = self.config.get('enable_hypothetical_analysis', True)
        self.max_index_columns = self.config.get('max_index_columns', 4)

        # Schema information cache
        self.schema_info = {}
        self.table_stats = {}
        self.column_stats = {}

        # Recommendation cache
        self.recommendation_cache = {}
        self.last_analysis_time = None

        logger.info("WorkloadIndexAdvisor initialized")

    def set_schema_info(self, schema_info: Dict[str, Dict]):
        """
        Set database schema information

        Args:
            schema_info: Dictionary with table and column information
        """
        self.schema_info = schema_info
        logger.info(f"Loaded schema information for {len(schema_info)} tables")

    def analyze_query_template(self, template_hash: str, template_text: str,
                              workload_data: Dict[str, Any]) -> QueryAnalysis:
        """
        Analyze a query template to extract index-relevant information

        Args:
            template_hash: Template hash
            template_text: Normalized template text
            workload_data: Associated workload data

        Returns:
            QueryAnalysis object
        """
        try:
            analysis = QueryAnalysis(
                template_hash=template_hash,
                template_text=template_text,
                tables_used=[],
                columns_accessed={},
                join_conditions=[],
                where_conditions=[],
                order_by_columns=[],
                group_by_columns=[],
                aggregate_functions=[],
                selectivity_estimates={},
                execution_frequency=workload_data.get('execution_count', 0),
                avg_execution_time_ms=workload_data.get('avg_execution_time_ms', 0.0),
                join_cardinality={}
            )

            # Parse the SQL template to extract patterns
            self._extract_sql_patterns(analysis)

            # Estimate selectivity for WHERE conditions
            self._estimate_selectivity(analysis)

            # Estimate join cardinality
            self._estimate_join_cardinality(analysis)

            return analysis

        except Exception as e:
            logger.error(f"Failed to analyze template {template_hash}: {e}")
            return QueryAnalysis(
                template_hash=template_hash,
                template_text=template_text,
                tables_used=[], columns_accessed={}, join_conditions=[],
                where_conditions=[], order_by_columns=[], group_by_columns=[],
                aggregate_functions=[], selectivity_estimates={},
                execution_frequency=0, avg_execution_time_ms=0.0,
                join_cardinality={}
            )

    def _extract_sql_patterns(self, analysis: QueryAnalysis):
        """Extract SQL patterns from template text"""
        template = analysis.template_text.upper()

        # Extract table names
        from_pattern = r'FROM\s+(\w+)'
        for match in re.finditer(from_pattern, template):
            table_name = match.group(1)
            if table_name not in ['DUAL']:
                analysis.tables_used.append(table_name)
                if table_name not in analysis.columns_accessed:
                    analysis.columns_accessed[table_name] = []

        # Extract JOIN tables and conditions
        join_pattern = r'(?:INNER|LEFT|RIGHT|FULL|CROSS)\s+JOIN\s+(\w+)(?:\s+AS\s+(\w+))?'
        for match in re.finditer(join_pattern, template):
            table_name = match.group(1)
            if table_name not in analysis.tables_used:
                analysis.tables_used.append(table_name)
            if table_name not in analysis.columns_accessed:
                analysis.columns_accessed[table_name] = []
            analysis.join_conditions.append(f"JOIN {table_name}")

        # Extract column references in WHERE conditions
        where_pattern = r'WHERE\s+([^;]+)'
        where_match = re.search(where_pattern, template)
        if where_match:
            where_clause = where_match.group(1)
            analysis.where_conditions.append(where_clause)
            # Extract column names from WHERE clause
            column_pattern = r'\b(\w+)\.\b(\w+)'
            for col_match in re.finditer(column_pattern, where_clause):
                table_name, column_name = col_match.groups()
                if table_name in analysis.columns_accessed:
                    if column_name not in analysis.columns_accessed[table_name]:
                        analysis.columns_accessed[table_name].append(column_name)

        # Extract ORDER BY columns
        order_pattern = r'ORDER\s+BY\s+([^;]+)'
        order_match = re.search(order_pattern, template)
        if order_match:
            order_clause = order_match.group(1)
            columns = [col.strip() for col in order_clause.split(',')]
            for col in columns:
                # Remove any function calls or expressions
                col_name = re.sub(r'\([^)]*\)', '', col).strip()
                if '.' in col_name:
                    table_name, column_name = col_name.split('.', 1)
                    if table_name in analysis.columns_accessed:
                        if column_name not in analysis.columns_accessed[table_name]:
                            analysis.columns_accessed[table_name].append(column_name)
                analysis.order_by_columns.append(col_name)

        # Extract GROUP BY columns
        group_pattern = r'GROUP\s+BY\s+([^;]+)'
        group_match = re.search(group_pattern, template)
        if group_match:
            group_clause = group_match.group(1)
            columns = [col.strip() for col in group_clause.split(',')]
            for col in columns:
                col_name = re.sub(r'\([^)]*\)', '', col).strip()
                if '.' in col_name:
                    table_name, column_name = col_name.split('.', 1)
                    if table_name in analysis.columns_accessed:
                        if column_name not in analysis.columns_accessed[table_name]:
                            analysis.columns_accessed[table_name].append(column_name)
                analysis.group_by_columns.append(col_name)

        # Extract aggregate functions
        agg_pattern = r'(COUNT|SUM|AVG|MIN|MAX|STDDEV)\s*\(\s*([^)]+)\s*\)'
        for match in re.finditer(agg_pattern, template):
            agg_function = f"{match.group(1)}({match.group(2)})"
            analysis.aggregate_functions.append(agg_function)

        # Remove duplicates and clean up
        analysis.tables_used = list(set(analysis.tables_used))
        for table in analysis.columns_accessed:
            analysis.columns_accessed[table] = list(set(analysis.columns_accessed[table]))
        analysis.order_by_columns = list(set(analysis.order_by_columns))
        analysis.group_by_columns = list(set(analysis.group_by_columns))
        analysis.aggregate_functions = list(set(analysis.aggregate_functions))

    def _estimate_selectivity(self, analysis: QueryAnalysis):
        """Estimate selectivity for WHERE conditions"""
        for table in analysis.tables_used:
            # Default selectivity estimates based on common patterns
            if analysis.where_conditions:
                # Count number of conditions
                condition_count = len(analysis.where_conditions)
                # More conditions generally mean lower selectivity
                analysis.selectivity_estimates[table] = max(0.01, 1.0 / (2 ** condition_count))
            else:
                # No WHERE clause, assume full table scan
                analysis.selectivity_estimates[table] = 1.0

    def _estimate_join_cardinality(self, analysis: QueryAnalysis):
        """Estimate join cardinality"""
        num_tables = len(analysis.tables_used)
        if num_tables > 1:
            # Simple heuristic: cardinality grows with join complexity
            base_cardinality = 1000  # Default estimate
            analysis.join_cardinality = {
                table: base_cardinality for table in analysis.tables_used
            }
            # Increase cardinality for tables involved in joins
            for table in analysis.tables_used:
                if any(table in condition for condition in analysis.join_conditions):
                    analysis.join_cardinality[table] *= 1.5

    def generate_index_candidates(self, analyses: List[QueryAnalysis],
                                 forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]] = None) -> List[IndexCandidate]:
        """
        Generate index candidates based on query analyses and forecasts

        Args:
            analyses: List of query analyses
            forecasts: Optional forecast data for workload prediction

        Returns:
            List of index candidates
        """
        candidates = []

        # Generate candidates based on different patterns
        candidates.extend(self._generate_high_frequency_candidates(analyses, forecasts))
        candidates.extend(self._generate_where_clause_candidates(analyses))
        candidates.extend(self._generate_join_candidates(analyses))
        candidates.extend(self._generate_order_by_candidates(analyses))
        candidates.extend(self._generate_aggregation_candidates(analyses))
        candidates.extend(self._generate_covering_indexes(analyses))

        # Remove duplicates and rank candidates
        candidates = self._deduplicate_candidates(candidates)
        candidates = self._rank_candidates(candidates, analyses, forecasts)

        # Apply filters
        candidates = [c for c in candidates if c.estimated_benefit >= self.min_benefit_threshold]

        logger.info(f"Generated {len(candidates)} index candidates")
        return candidates

    def _generate_high_frequency_candidates(self, analyses: List[QueryAnalysis],
                                            forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[IndexCandidate]:
        """Generate candidates for high-frequency queries"""
        candidates = []

        # Sort by execution frequency
        sorted_analyses = sorted(analyses, key=lambda x: x.execution_frequency, reverse=True)

        for analysis in sorted_analyses[:20]:  # Top 20 most frequent
            if analysis.execution_frequency < 100:  # Minimum threshold
                continue

            # Generate single-column indexes for heavily accessed columns
            for table, columns in analysis.columns_accessed.items():
                for column in columns[:2]:  # Top 2 columns per table
                    candidate = self._create_single_column_candidate(
                        table, column, analysis, IndexScenario.HIGH_FREQUENCY
                    )
                    if candidate:
                        candidates.append(candidate)

            # Generate multi-column indexes for common column combinations
            if len(analysis.columns_accessed) > 0:
                candidate = self._create_multi_column_candidate(
                    analysis.columns_accessed, analysis, IndexScenario.HIGH_FREQUENCY
                )
                if candidate:
                    candidates.append(candidate)

        return candidates

    def _generate_where_clause_candidates(self, analyses: List[QueryAnalysis],
                                         forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[IndexCandidate]:
        """Generate candidates for WHERE clause optimization"""
        candidates = []

        for analysis in analyses:
            if not analysis.where_conditions:
                continue

            # Focus on queries with selective WHERE clauses
            for table, selectivity in analysis.selectivity_estimates.items():
                if selectivity < 0.5:  # Selective enough to benefit from index
                    for column in analysis.columns_accessed.get(table, []):
                        candidate = self._create_single_column_candidate(
                            table, column, analysis, IndexScenario.SELECTIVITY_LOW
                        )
                        if candidate:
                            candidates.append(candidate)

        return candidates

    def _generate_join_candidates(self, analyses: List[QueryAnalysis],
                                  forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[IndexCandidate]:
        """Generate candidates for join optimization"""
        candidates = []

        for analysis in analyses:
            if len(analysis.tables_used) < 2:
                continue  # No joins

            # Generate indexes on foreign key columns for joins
            for table in analysis.tables_used:
                for column in analysis.columns_accessed.get(table, []):
                    if self._is_likely_foreign_key(table, column):
                        candidate = self._create_single_column_candidate(
                            table, column, analysis, IndexScenario.JOIN_OPTIMIZATION
                        )
                        if candidate:
                            candidates.append(candidate)

            # Generate composite indexes for join columns
            if len(analysis.tables_used) == 2:
                table1, table2 = analysis.tables_used[:2]
                columns1 = analysis.columns_accessed.get(table1, [])
                columns2 = analysis.columns_accessed.get(table2, [])

                if columns1 and columns2:
                    # Create cross-table index (not really possible, but we can index join columns)
                    candidate = self._create_join_candidate(table1, table2, columns1[0], columns2[0], analysis)
                    if candidate:
                        candidates.append(candidate)

        return candidates

    def _generate_order_by_candidates(self, analyses: List[QueryAnalysis],
                                      forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[IndexCandidate]:
        """Generate candidates for ORDER BY optimization"""
        candidates = []

        for analysis in analyses:
            if not analysis.order_by_columns:
                continue

            for column in analysis.order_by_columns:
                if '.' in column:
                    table_name, column_name = column.split('.', 1)
                    if table_name in analysis.columns_accessed:
                        candidate = self._create_single_column_candidate(
                            table_name, column_name, analysis, IndexScenario.ORDER_BY
                        )
                        if candidate:
                            candidates.append(candidate)

        return candidates

    def _generate_aggregation_candidates(self, analyses: List[QueryAnalysis],
                                         forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[IndexCandidate]:
        """Generate candidates for aggregation optimization"""
        candidates = []

        for analysis in analyses:
            if not analysis.group_by_columns:
                continue

            # Index on GROUP BY columns
            for column in analysis.group_by_columns:
                if '.' in column:
                    table_name, column_name = column.split('.', 1)
                    if table_name in analysis.columns_accessed:
                        candidate = self._create_single_column_candidate(
                            table_name, column_name, analysis, IndexScenario.AGGREGATION
                        )
                        if candidate:
                            candidates.append(candidate)

        return candidates

    def _generate_covering_indexes(self, analyses: List[QueryAnalysis],
                                    forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[IndexCandidate]:
        """Generate covering indexes for common query patterns"""
        candidates = []

        for analysis in analyses:
            if len(analysis.tables_used) == 1:
                table = analysis.tables_used[0]
                columns = analysis.columns_accessed.get(table, [])

                # Generate covering index for SELECT columns
                select_columns = self._extract_select_columns(analysis.template_text)
                index_columns = []

                # Add WHERE clause columns
                if analysis.where_conditions:
                    index_columns.extend(columns[:2])  # Top 2 WHERE columns

                # Add ORDER BY columns
                if analysis.order_by_columns:
                    for col in analysis.order_by_columns:
                        if '.' in col:
                            table_name, column_name = col.split('.', 1)
                            if table_name == table and column_name not in index_columns:
                                index_columns.append(column_name)

                # Add GROUP BY columns
                if analysis.group_by_columns:
                    for col in analysis.group_by_columns:
                        if '.' in col:
                            table_name, column_name = col.split('.', 1)
                            if table_name == table and column_name not in index_columns:
                                index_columns.append(column_name)

                # Create covering index if we have enough columns
                if len(index_columns) >= 2 and len(index_columns) <= self.max_index_columns:
                    candidate = self._create_multi_column_candidate(
                        {table: index_columns}, analysis, IndexScenario.SELECTIVITY_LOW
                    )
                    if candidate:
                        candidates.append(candidate)

        return candidates

    def _create_single_column_candidate(self, table: str, column: str, analysis: QueryAnalysis,
                                        scenario: IndexScenario) -> Optional[IndexCandidate]:
        """Create a single-column index candidate"""
        try:
            # Estimate index size
            estimated_size = self._estimate_index_size(table, [column])
            if estimated_size > 1000:  # Skip very large indexes
                return None

            # Calculate benefit
            benefit = self._calculate_index_benefit(table, [column], analysis, scenario)
            if benefit < self.min_benefit_threshold:
                return None

            # Calculate confidence
            confidence = self._calculate_confidence_score(table, [column], analysis, scenario)

            return IndexCandidate(
                table_name=table,
                columns=[column],
                index_type=IndexType.BTREE,
                scenario=scenario,
                estimated_benefit=benefit,
                confidence_score=confidence,
                estimated_size_mb=estimated_size,
                creation_cost=estimated_size * 0.1,  # 10% of size for creation
                maintenance_overhead=estimated_size * self.maintenance_overhead_factor,
                sql_statements=[f"CREATE INDEX idx_{table}_{column} ON {table}({column})"],
                supporting_templates=[analysis.template_hash],
                supporting_queries=[],
                estimated_impact_queries=analysis.execution_frequency,
                estimated_cost_reduction=analysis.avg_execution_time_ms * benefit * 0.01
            )

        except Exception as e:
            logger.error(f"Failed to create candidate for {table}.{column}: {e}")
            return None

    def _create_multi_column_candidate(self, columns_dict: Dict[str, List[str]], analysis: QueryAnalysis,
                                      scenario: IndexScenario) -> Optional[IndexCandidate]:
        """Create a multi-column index candidate"""
        try:
            # Select the table with most columns
            best_table = max(columns_dict.keys(), key=lambda x: len(columns_dict[x]))
            columns = columns_dict[best_table][:self.max_index_columns]

            if len(columns) < 2:
                return None

            # Estimate index size
            estimated_size = self._estimate_index_size(best_table, columns)
            if estimated_size > 1000:  # Skip very large indexes
                return None

            # Calculate benefit
            benefit = self._calculate_index_benefit(best_table, columns, analysis, scenario)
            if benefit < self.min_benefit_threshold:
                return None

            # Calculate confidence
            confidence = self._calculate_confidence_score(best_table, columns, analysis, scenario)

            index_name = f"idx_{best_table}_{'_'.join(columns[:3])}"  # Limit name length
            columns_str = ", ".join(columns)

            return IndexCandidate(
                table_name=best_table,
                columns=columns,
                index_type=IndexType.BTREE,
                scenario=scenario,
                estimated_benefit=benefit,
                confidence_score=confidence,
                estimated_size_mb=estimated_size,
                creation_cost=estimated_size * 0.15,  # Higher cost for composite indexes
                maintenance_overhead=estimated_size * self.maintenance_overhead_factor * 1.2,
                sql_statements=[f"CREATE INDEX {index_name} ON {best_table}({columns_str})"],
                supporting_templates=[analysis.template_hash],
                supporting_queries=[],
                estimated_impact_queries=analysis.execution_frequency,
                estimated_cost_reduction=analysis.avg_execution_time_ms * benefit * 0.02  # Higher benefit for composite
            )

        except Exception as e:
            logger.error(f"Failed to create multi-column candidate: {e}")
            return None

    def _create_join_candidate(self, table1: str, table2: str, col1: str, col2: str,
                                analysis: QueryAnalysis) -> Optional[IndexCandidate]:
        """Create a join optimization candidate"""
        # This is conceptual - we can't really create cross-table indexes
        # But we can recommend indexing the foreign key columns
        return self._create_single_column_candidate(
            table1, col1, analysis, IndexScenario.JOIN_OPTIMIZATION
        )

    def _estimate_index_size(self, table: str, columns: List[str]) -> float:
        """Estimate index size in MB"""
        # Simple heuristic: 100 bytes per column + 50 bytes base per row
        # Assume average row count and data types
        base_size = 50
        column_size = len(columns) * 100
        estimated_rows = 10000  # Default estimate

        total_size_bytes = estimated_rows * (base_size + column_size)
        return total_size_bytes / (1024 * 1024)  # Convert to MB

    def _calculate_index_benefit(self, table: str, columns: List[str], analysis: QueryAnalysis,
                               scenario: IndexScenario) -> float:
        """Calculate estimated benefit of an index"""
        benefit = 0.0

        # Base benefit from frequency
        frequency_factor = min(1.0, analysis.execution_frequency / 1000.0)
        benefit += frequency_factor * 0.3

        # Benefit from selectivity improvement
        if scenario == IndexScenario.SELECTIVITY_LOW:
            table_selectivity = analysis.selectivity_estimates.get(table, 1.0)
            benefit += (1.0 - table_selectivity) * 0.4

        # Benefit from ORDER BY optimization
        if scenario == IndexScenario.ORDER_BY:
            benefit += 0.3  # ORDER BY is very beneficial

        # Benefit from join optimization
        if scenario == IndexScenario.JOIN_OPTIMIZATION:
            benefit += 0.25

        # Benefit from aggregation optimization
        if scenario == IndexScenario.AGGREGATION:
            benefit += 0.25

        # Benefit from high frequency
        if scenario == IndexScenario.HIGH_FREQUENCY:
            benefit += 0.2

        # Size penalty
        index_size = self._estimate_index_size(table, columns)
        size_penalty = min(0.3, index_size * self.index_size_penalty)
        benefit -= size_penalty

        # Column count penalty (composite indexes are more expensive)
        column_penalty = (len(columns) - 1) * 0.05
        benefit -= column_penalty

        return max(0.0, benefit)

    def _calculate_confidence_score(self, table: str, columns: List[str], analysis: QueryAnalysis,
                                   scenario: IndexScenario) -> float:
        """Calculate confidence score for the recommendation"""
        confidence = 0.5  # Base confidence

        # Higher confidence for more frequently executed queries
        if analysis.execution_frequency > 100:
            confidence += 0.2
        elif analysis.execution_frequency > 10:
            confidence += 0.1

        # Higher confidence for single-column indexes
        if len(columns) == 1:
            confidence += 0.2
        elif len(columns) == 2:
            confidence += 0.1

        # Higher confidence for well-understood patterns
        if scenario in [IndexScenario.HIGH_FREQUENCY, IndexScenario.ORDER_BY]:
            confidence += 0.1

        # Higher confidence if we have schema information
        if self.schema_info and table in self.schema_info:
            confidence += 0.1

        return min(1.0, confidence)

    def _is_likely_foreign_key(self, table: str, column: str) -> bool:
        """Check if a column is likely a foreign key"""
        # Simple heuristic: columns ending with '_id' are often foreign keys
        return column.endswith('_id') and column != f"{table}_id"

    def _extract_select_columns(self, template_text: str) -> List[str]:
        """Extract column names from SELECT clause"""
        select_pattern = r'SELECT\s+(.+?)\s+FROM'
        match = re.search(select_pattern, template_text, re.IGNORECASE)
        if match:
            select_clause = match.group(1)
            # Simple extraction - doesn't handle all cases
            columns = [col.strip() for col in select_clause.split(',')]
            return [col for col in columns if col != '*']
        return []

    def _deduplicate_candidates(self, candidates: List[IndexCandidate]) -> List[IndexCandidate]:
        """Remove duplicate index candidates"""
        seen = set()
        unique_candidates = []

        for candidate in candidates:
            # Create a unique identifier
            identifier = f"{candidate.table_name}_{'_'.join(sorted(candidate.columns))}"
            if identifier not in seen:
                seen.add(identifier)
                unique_candidates.append(candidate)

        return unique_candidates

    def _rank_candidates(self, candidates: List[IndexCandidate],
                           analyses: List[QueryAnalysis],
                           forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]]) -> List[IndexCandidate]:
        """Rank index candidates by expected benefit"""
        for candidate in candidates:
            # Calculate total impact score
            candidate.estimated_impact_queries = 0
            candidate.estimated_cost_reduction = 0.0

            # Sum impact from supporting templates
            for analysis in analyses:
                if analysis.template_hash in candidate.supporting_templates:
                    candidate.estimated_impact_queries += analysis.execution_frequency
                    candidate.estimated_cost_reduction += (
                        analysis.avg_execution_time_ms * candidate.estimated_benefit * 0.01
                    )

            # Add forecast impact if available
            if forecasts:
                template_hash = candidate.supporting_templates[0] if candidate.supporting_templates else None
                if template_hash and template_hash in forecasts:
                    forecast_data = forecasts[template_hash]
                    total_future_queries = sum(val for _, val in forecast_data)
                    candidate.estimated_impact_queries += total_future_queries

            # Adjust benefit based on impact
            if candidate.estimated_impact_queries > 0:
                candidate.estimated_benefit = min(1.0, candidate.estimated_benefit *
                                            (candidate.estimated_impact_queries / 1000.0))

        # Sort by benefit score (descending)
        return sorted(candidates, key=lambda x: x.estimated_benefit, reverse=True)

    def get_recommendations(self, analyses: List[QueryAnalysis],
                           forecasts: Optional[Dict[str, List[Tuple[datetime, float]]]] = None,
                           max_recommendations: int = 50) -> List[IndexCandidate]:
        """
        Get index recommendations

        Args:
            analyses: Query analyses
            forecasts: Optional forecast data
            max_recommendations: Maximum number of recommendations

        Returns:
            List of ranked index recommendations
        """
        logger.info(f"Generating index recommendations from {len(analyses)} query analyses")

        # Generate candidates
        candidates = self.generate_index_candidates(analyses, forecasts)

        # Apply per-table limits
        table_counts = defaultdict(int)
        filtered_candidates = []

        for candidate in candidates:
            table_counts[candidate.table_name] += 1
            if table_counts[candidate.table_name] <= self.max_indexes_per_table:
                filtered_candidates.append(candidate)

        # Return top recommendations
        return filtered_candidates[:max_recommendations]

    def explain_recommendation(self, candidate: IndexCandidate) -> Dict[str, Any]:
        """
        Generate explanation for an index recommendation

        Args:
            candidate: Index candidate to explain

        Returns:
            Dictionary with explanation details
        """
        explanation = {
            "recommendation": f"Create {candidate.index_type.value} index on {candidate.table_name}({', '.join(candidate.columns)})",
            "scenario": candidate.scenario.value,
            "estimated_benefit": candidate.estimated_benefit,
            "confidence_score": candidate.confidence_score,
            "estimated_size_mb": candidate.estimated_size_mb,
            "creation_cost": candidate.creation_cost,
            "maintenance_overhead": candidate.maintenance_overhead,
            "impact_queries": candidate.estimated_impact_queries,
            "cost_reduction_ms": candidate.estimated_cost_reduction,
            "sql_statements": candidate.sql_statements,
            "reasoning": []
        }

        # Add reasoning based on scenario
        if candidate.scenario == IndexScenario.HIGH_FREQUENCY:
            explanation["reasoning"].append(
                f"High-frequency query executed {candidate.estimated_impact_queries} times"
            )
        elif candidate.scenario == IndexScenario.SELECTIVITY_LOW:
            explanation["reasoning"].append(
                "Will significantly reduce table scan cost for selective queries"
            )
        elif candidate.scenario == IndexScenario.ORDER_BY:
            explanation["reasoning"].append(
                "Will eliminate sorting costs for ORDER BY operations"
            )
        elif candidate.scenario == IndexScenario.JOIN_OPTIMIZATION:
            explanation["reasoning"].append(
                "Will improve join performance by indexing join columns"
            )
        elif candidate.scenario == IndexScenario.AGGREGATION:
            explanation["reasoning"].append(
                "Will accelerate GROUP BY and aggregation operations"
            )

        return explanation