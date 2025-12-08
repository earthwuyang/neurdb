"""
Cost-Based Index Advisor using HypoPG for Real Cost Estimation
Implements DTA-inspired progressive optimization with PostgreSQL's actual cost model
"""

import re
import json
import logging
import time
from typing import Dict, List, Tuple, Set, Optional, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import numpy as np
from collections import defaultdict
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlparse

logger = logging.getLogger(__name__)


class IndexType(Enum):
    """Supported index types"""
    BTREE = "btree"
    HASH = "hash"
    GIN = "gin"
    GIST = "gist"
    BRIN = "brin"


@dataclass
class IndexCandidate:
    """Represents a potential index for evaluation"""
    table_name: str
    columns: List[str]
    index_type: IndexType
    estimated_size_mb: float = 0.0
    creation_cost: float = 0.0


@dataclass
class IndexConfiguration:
    """Represents a set of indexes for cost evaluation"""
    indexes: List[IndexCandidate]
    total_cost: float = 0.0
    storage_cost: float = 0.0
    maintenance_cost: float = 0.0
    benefit_score: float = 0.0

    def __add__(self, other_index):
        """Add an index to configuration"""
        new_config = IndexConfiguration(
            indexes=self.indexes + [other_index],
            storage_cost=self.storage_cost + other_index.estimated_size_mb,
            maintenance_cost=self.maintenance_cost + other_index.creation_cost
        )
        return new_config


@dataclass
class QueryCostInfo:
    """Cost information for a single query"""
    query: str
    template_hash: str
    frequency: int
    base_cost: float
    base_time: float
    base_io: int
    optimized_cost: Optional[float] = None
    optimized_time: Optional[float] = None
    optimized_io: Optional[int] = None


@dataclass
class ProgressiveResult:
    """Result from progressive optimization with quality history"""
    best_solution: IndexConfiguration
    quality_improvements: List[Tuple[float, float]]  # (time, benefit_score)
    optimization_time: float


class HypoPGCostModel:
    """Cost model integration using hypopg for real PostgreSQL cost estimation"""

    def __init__(self, db_connection_params):
        self.db_params = db_connection_params
        self.hypopg_enabled = True

    def enable_hypog_for_session(self):
        """Enable hypopg for current database session"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Reset any existing hypothetical indexes
                cursor.execute("SELECT hypopg_reset();")
                # Ensure hypopg is enabled
                cursor.execute("SET hypopg.enabled = true;")
                logger.info("HypoPG enabled for cost estimation")
            conn.close()
        except Exception as e:
            logger.error(f"Failed to enable HypoPG: {e}")
            self.hypopg_enabled = False

    def create_hypothetical_index(self, index_candidate: IndexCandidate) -> Optional[int]:
        """Create a hypothetical index using hypopg"""
        if not self.hypopg_enabled:
            return None

        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Build CREATE INDEX statement
                columns_str = ", ".join(index_candidate.columns)
                # Generate a unique index name for HypoPG
                index_name = f"hypopg_{index_candidate.table_name}_{'_'.join(index_candidate.columns)}"

                # HypoPG doesn't support USING clause for BTREE (which is default)
                if index_candidate.index_type.value == 'btree':
                    create_sql = f"CREATE INDEX {index_name} ON {index_candidate.table_name} ({columns_str})"
                else:
                    create_sql = f"CREATE INDEX {index_name} ON {index_candidate.table_name} ({columns_str}) USING {index_candidate.index_type.value}"

                # Create hypothetical index
                logger.info(f"Attempting to create HypoPG index: {create_sql}")
                cursor.execute(f"SELECT hypopg_create_index('{create_sql}');")
                result = cursor.fetchone()
                logger.info(f"HypPG result: {result}")

                if result and result[0] > 0:
                    index_oid = result[0]
                    conn.close()
                    return index_oid
                else:
                    conn.close()
                    return None

        except Exception as e:
            logger.error(f"Failed to create hypothetical index: {e}")
            return None

    def drop_hypothetical_index(self, index_oid: int):
        """Drop a hypothetical index"""
        if not self.hypopg_enabled:
            return

        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT hypopg_drop_index({index_oid});")
            conn.close()
        except Exception as e:
            logger.error(f"Failed to drop hypothetical index {index_oid}: {e}")

    def estimate_query_cost(self, query: str, index_config: IndexConfiguration) -> Dict[str, float]:
        """Estimate query execution cost using hypopg"""
        if not self.hypopg_enabled:
            raise RuntimeError("HypoPG is required but not available. Cannot estimate query cost without HypoPG.")

        try:
            # Create all indexes in the configuration
            index_oids = []
            for index in index_config.indexes:
                oid = self.create_hypothetical_index(index)
                if oid:
                    index_oids.append(oid)

            # Get execution plan with hypothetical indexes
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                explain_sql = f"EXPLAIN (FORMAT JSON, ANALYZE, BUFFERS) {query}"
                cursor.execute(explain_sql)
                plan_result = cursor.fetchone()

            conn.close()

            # Clean up hypothetical indexes
            for oid in index_oids:
                self.drop_hypothetical_index(oid)

            if plan_result and plan_result[0]:
                # Handle both string and already-parsed JSON
                if isinstance(plan_result[0], str):
                    plan_data = json.loads(plan_result[0])[0]['Plan']
                else:
                    # Psycopg2 already parsed the JSON
                    plan_data = plan_result[0][0]['Plan']

                return {
                    'total_cost': plan_data.get('Total Cost', 0.0),
                    'execution_time': plan_data.get('Actual Total Time', 0.0),
                    'shared_blocks': plan_data.get('Shared Hit Blocks', 0) + plan_data.get('Shared Read Blocks', 0),
                    'plan_nodes': len([node for node in self._extract_all_plan_nodes(plan_data)])
                }

        except Exception as e:
            logger.error(f"Failed to estimate query cost: {e}")
            return {'total_cost': 0.0, 'execution_time': 0.0, 'shared_blocks': 0}

    def _extract_all_plan_nodes(self, plan_node):
        """Extract all plan nodes from PostgreSQL explain output"""
        nodes = [plan_node]
        if 'Plans' in plan_node:
            for child_plan in plan_node['Plans']:
                nodes.extend(self._extract_all_plan_nodes(child_plan))
        return nodes

    

class IndexSearchSpace:
    """Systematic enumeration and pruning of candidate index configurations"""

    def __init__(self, max_indexes_per_table=3, max_total_indexes=10):
        self.max_indexes_per_table = max_indexes_per_table
        self.max_total_indexes = max_total_indexes

    def generate_single_column_candidates(self, schema_info: Dict, query_workload: List[QueryCostInfo]) -> List[IndexCandidate]:
        """Generate single-column index candidates based on workload analysis"""
        candidates = []
        column_usage = defaultdict(int)

        logger.info(f"Analyzing {len(query_workload)} queries for column usage")

        # Analyze workload to identify frequently accessed columns
        for i, query_info in enumerate(query_workload):
            logger.debug(f"Processing query {i+1}: {query_info.query[:100]}...")
            query_columns = self._extract_column_usage(query_info.query)
            logger.debug(f"Query {i+1} columns found: {dict(query_columns)}")
            column_usage.update(query_columns)

        logger.info(f"Column usage summary: {dict(column_usage)}")

        # Sort columns by usage frequency
        sorted_columns = sorted(column_usage.items(), key=lambda x: x[1], reverse=True)
        logger.info(f"Sorted columns: {sorted_columns}")

        # Generate index candidates for most frequently used columns
        for table_column, usage_count in sorted_columns[:50]:  # Limit to top 50 columns
            if usage_count >= 1:  # Lower minimum threshold for testing
                # Parse table.column format
                if '.' in table_column:
                    table_name, column_name = table_column.split('.', 1)
                    logger.info(f"Creating candidate for {table_name}.{column_name} (usage: {usage_count})")
                    candidates.append(IndexCandidate(
                        table_name=table_name,
                        columns=[column_name],  # Use actual column name
                        index_type=IndexType.BTREE,
                        estimated_size_mb=self._estimate_index_size(table_name),
                        creation_cost=10.0
                    ))

        logger.info(f"Generated {len(candidates)} single-column candidates")
        return candidates

    def generate_multi_column_candidates(self, schema_info: Dict, query_workload: List[QueryCostInfo]) -> List[IndexCandidate]:
        """Generate compound index candidates based on column co-occurrence"""
        candidates = []
        co_occurrence = defaultdict(lambda: defaultdict(int))

        # Analyze column co-occurrence in WHERE clauses
        for query_info in query_workload:
            where_columns = self._extract_where_columns(query_info.query)
            for i, col1 in enumerate(where_columns):
                for col2 in where_columns[i+1:]:
                    co_occurrence[col1][col2] += query_info.frequency

        # Generate compound indexes for frequently co-occurring columns
        for col1, co_cols in co_occurrence.items():
            for col2, frequency in co_cols.items():
                if frequency >= 10:  # Co-occurrence threshold
                    candidates.append(IndexCandidate(
                        table_name=self._extract_table_name(col1),
                        columns=[col1, col2],
                        index_type=IndexType.BTREE,
                        estimated_size_mb=self._estimate_compound_index_size([col1, col2]),
                        creation_cost=15.0
                    ))

        return sorted(candidates, key=lambda x: x.estimated_size_mb, reverse=True)[:20]  # Top 20

    def _extract_column_usage(self, query: str) -> Dict[str, int]:
        """Extract column references from query using robust SQL parsing"""
        columns = defaultdict(int)

        try:
            # Parse the SQL query
            parsed = sqlparse.parse(query)[0]

            # Helper function to extract columns from identifiers
            def extract_columns_from_token(token):
                if isinstance(token, sqlparse.sql.Identifier):
                    # Handle table.column format
                    token_str = str(token)
                    if '.' in token_str:
                        parts = token_str.split('.')
                        if len(parts) >= 2:
                            table_name = parts[0].strip('"')
                            column_name = '.'.join(parts[1:]).strip('"')
                            return f"{table_name}.{column_name}"
                    return token_str
                elif isinstance(token, sqlparse.sql.IdentifierList):
                    # Handle multiple columns
                    cols = []
                    for identifier in token.get_identifiers():
                        col = extract_columns_from_token(identifier)
                        if col:
                            cols.append(col)
                    return cols
                elif isinstance(token, sqlparse.sql.Function):
                    # Handle function calls like COUNT(t.id)
                    cols = []
                    for param in token.get_parameters():
                        col = extract_columns_from_token(param)
                        if col:
                            cols.append(col)
                    return cols
                return None

            # Extract columns from identifiers recursively
            def extract_identifiers(tokens):
                for token in tokens:
                    if isinstance(token, sqlparse.sql.Identifier):
                        col = extract_columns_from_token(token)
                        if col:
                            if isinstance(col, list):
                                for c in col:
                                    if c and len(c) > 0:
                                        columns[c] += 1
                            elif isinstance(col, str) and len(col) > 0:
                                columns[col] += 1
                    # Also handle comparison expressions in WHERE clause
                    elif isinstance(token, sqlparse.sql.Comparison):
                        # Extract columns from left side of comparison (e.g., production_year = 2000)
                        if hasattr(token, 'left'):
                            left_cols = extract_columns_from_token(token.left)
                            if left_cols:
                                if isinstance(left_cols, list):
                                    for c in left_cols:
                                        if c and len(c) > 0:
                                            columns[c] += 1
                                elif isinstance(left_cols, str) and len(left_cols) > 0:
                                    columns[left_cols] += 1
                    elif isinstance(token, sqlparse.sql.IdentifierList):
                        extract_identifiers(token.get_identifiers())
                    elif isinstance(token, sqlparse.sql.Function):
                        # Check function parameters
                        for param in token.get_parameters():
                            col = extract_columns_from_token(param)
                            if col:
                                if isinstance(col, list):
                                    for c in col:
                                        if c and len(c) > 0:
                                            columns[c] += 1
                                elif isinstance(col, str) and len(col) > 0:
                                    columns[col] += 1
                    # Recursively check child tokens
                    if hasattr(token, 'tokens'):
                        extract_identifiers(token.tokens)

            # Start extraction from the parsed query
            extract_identifiers(parsed.tokens)

        except Exception as e:
            logger.warning(f"SQL parsing failed for query: {query[:100]}... - {e}")
            # Fallback to simple regex extraction
            column_pattern = r'\b(\w+)\.(\w+)\b'
            for match in re.finditer(column_pattern, query):
                table, column = match.groups()
                columns[f"{table}.{column}"] += 1

        return columns

    def _extract_where_clause(self, query: str) -> Optional[str]:
        """Extract WHERE clause from query using robust parsing"""
        try:
            parsed = sqlparse.parse(query)[0]

            # Find WHERE clause using sqlparse
            where_clause = None
            for token in parsed.tokens:
                if token.ttype is sqlparse.tokens.Keyword and str(token).upper() == 'WHERE':
                    # Get the content after WHERE until next major keyword
                    where_start = parsed.token_index(token) + 1
                    where_tokens = []

                    for i in range(where_start, len(parsed.tokens)):
                        current_token = parsed.tokens[i]

                        # Stop at major keywords that end WHERE clause
                        if (current_token.ttype is sqlparse.tokens.Keyword and
                            str(current_token).upper() in ('GROUP', 'ORDER', 'LIMIT', 'HAVING')):
                            break

                        if not current_token.is_whitespace:
                            where_tokens.append(str(current_token))

                    if where_tokens:
                        where_clause = ' '.join(where_tokens)
                        break

            return where_clause

        except Exception as e:
            logger.warning(f"WHERE clause extraction failed: {e}")
            # Fallback to regex
            where_match = re.search(r'WHERE\s+(.+?)(?:\s+GROUP\s+BY|\s+ORDER\s+BY|\s+LIMIT|$)', query, re.IGNORECASE)
            return where_match.group(1) if where_match else None

    def _extract_where_columns(self, query: str) -> List[str]:
        """Extract columns from WHERE clause"""
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+GROUP\s+BY|\s+ORDER\s+BY|\s+LIMIT|$)', query, re.IGNORECASE)
        if not where_match:
            return []

        where_clause = where_match.group(1)
        columns = []
        column_pattern = r'\b(\w+)\.(\w+)\b'
        for match in re.finditer(column_pattern, where_clause):
            columns.append(f"{match.group(1)}.{match.group(2)}")
        return columns

    def _extract_table_name(self, column_reference: str) -> str:
        """Extract table name from column reference"""
        if '.' in column_reference:
            return column_reference.split('.')[0]
        return 'unknown'

    def _estimate_index_size(self, table_name: str) -> float:
        """Estimate index size in MB"""
        # TODO: Use actual table statistics
        return 10.0  # Placeholder estimation

    def _estimate_compound_index_size(self, columns: List[str]) -> float:
        """Estimate compound index size in MB"""
        base_size = self._estimate_index_size('unknown')
        return base_size * (1.0 + 0.1 * (len(columns) - 1))  # 10% increase per additional column


class AnytimeIndexOptimizer:
    """DTA-style progressive optimization algorithm"""

    def __init__(self, db_connection_params, time_limit_seconds=60):
        self.db_params = db_connection_params
        self.time_limit_seconds = time_limit_seconds
        self.cost_model = HypoPGCostModel(db_connection_params)
        self.search_space = IndexSearchSpace()
        self.baseline_costs = {}  # Initialize baseline_costs storage

    def optimize_progressively(self, workload: List[QueryCostInfo], schema_info: Dict) -> ProgressiveResult:
        """Main progressive optimization loop"""
        start_time = time.time()
        best_solution = IndexConfiguration(indexes=[])
        quality_improvements = []

        logger.info(f"Starting progressive optimization for {len(workload)} queries")

        # Phase 1: Enable hypopg
        self.cost_model.enable_hypog_for_session()

        # Phase 2: Generate candidates
        single_col_candidates = self.search_space.generate_single_column_candidates(schema_info, workload)
        multi_col_candidates = self.search_space.generate_multi_column_candidates(schema_info, workload)
        all_candidates = single_col_candidates + multi_col_candidates

        logger.info(f"Generated {len(all_candidates)} candidate indexes")

        # Phase 3: Calculate baseline costs
        self.baseline_costs = self._calculate_baseline_costs(workload)
        # Empty configuration should always have benefit score of 0
        best_solution.benefit_score = 0.0

        quality_improvements.append((0, best_solution.benefit_score))

        # Phase 4: Greedy single-index optimization
        greedy_solution = self.greedy_single_index(workload, all_candidates)
        if greedy_solution.benefit_score > best_solution.benefit_score:
            improvement_time = time.time() - start_time
            best_solution = greedy_solution
            quality_improvements.append((improvement_time, best_solution.benefit_score))
            logger.info(f"Greedy improvement: {best_solution.benefit_score:.2f}")

        # Phase 5: Iterative local search
        while time.time() - start_time < self.time_limit_seconds * 0.7:  # Use 70% of time for local search
            improved_solution = self.local_search(best_solution, workload, all_candidates[:50])  # Limit to top 50 candidates
            if improved_solution.benefit_score > best_solution.benefit_score:
                improvement_time = time.time() - start_time
                best_solution = improved_solution
                quality_improvements.append((improvement_time, best_solution.benefit_score))
                logger.info(f"Local search improvement: {best_solution.benefit_score:.2f} at {improvement_time:.1f}s")

        optimization_time = time.time() - start_time
        logger.info(f"Optimization completed in {optimization_time:.1f}s, final benefit: {best_solution.benefit_score:.2f}")
        logger.info(f"Best solution contains {len(best_solution.indexes)} indexes")
        if len(best_solution.indexes) > 0:
            for idx in best_solution.indexes:
                logger.info(f"  - Index: {idx.table_name}({', '.join(idx.columns)}), benefit: {idx.benefit_score:.4f}")
        else:
            logger.info("  - No indexes in best solution (empty configuration)")

        return ProgressiveResult(best_solution, quality_improvements, optimization_time)

    def greedy_single_index(self, workload: List[QueryCostInfo], candidates: List[IndexCandidate]) -> IndexConfiguration:
        """Greedy algorithm adding best single index repeatedly"""
        current_config = IndexConfiguration(indexes=[])
        remaining_budget = self.search_space.max_total_indexes

        while remaining_budget > 0 and candidates:
            best_candidate = None
            best_improvement = 0.0

            # Evaluate adding each remaining candidate
            for candidate in candidates:
                if candidate in current_config.indexes:
                    continue

                test_config = current_config + candidate
                improvement = self._calculate_improvement(workload, current_config, test_config)

                if improvement > best_improvement:
                    best_improvement = improvement
                    best_candidate = candidate

            if best_candidate and best_improvement > 0.0:  # Any positive improvement threshold
                current_config = current_config + best_candidate
                remaining_budget -= 1
                candidates.remove(best_candidate)
                logger.info(f"Added index: {best_candidate.table_name}({', '.join(best_candidate.columns)}) improvement: {best_improvement:.4f}")
            else:
                logger.info(f"No index added - best_improvement: {best_improvement:.4f}, remaining candidates: {len(candidates)}")
                break

        return current_config

    def local_search(self, current_solution: IndexConfiguration, workload: List[QueryCostInfo], candidates: List[IndexCandidate]) -> IndexConfiguration:
        """Local search starting from current solution"""
        best_solution = current_solution
        best_score = self._calculate_solution_cost(workload, {}, current_solution)

        # Try removing existing indexes
        for i, existing_index in enumerate(current_solution.indexes):
            test_config = IndexConfiguration(indexes=current_solution.indexes[:i] + current_solution.indexes[i+1:])
            score = self._calculate_solution_cost(workload, {}, test_config)
            if score > best_score:
                best_solution = test_config
                best_score = score

        # Try replacing existing indexes
        for i, existing_index in enumerate(current_solution.indexes):
            for candidate in candidates:
                if candidate in current_solution.indexes:
                    continue

                test_config = IndexConfiguration(
                    indexes=current_solution.indexes[:i] + [candidate] + current_solution.indexes[i+1:]
                )
                score = self._calculate_solution_cost(workload, {}, test_config)
                if score > best_score:
                    best_solution = test_config
                    best_score = score

        return best_solution

    def _calculate_baseline_costs(self, workload: List[QueryCostInfo]) -> Dict[str, QueryCostInfo]:
        """Calculate baseline costs without any indexes"""
        baseline_costs = {}
        for query_info in workload:
            cost_info = self.cost_model.estimate_query_cost(query_info.query, IndexConfiguration(indexes=[]))
            baseline_costs[query_info.template_hash] = QueryCostInfo(
                query=query_info.query,
                template_hash=query_info.template_hash,
                frequency=query_info.frequency,
                base_cost=cost_info['total_cost'],
                base_time=cost_info['execution_time'],
                base_io=cost_info['shared_blocks']
            )
        return baseline_costs

    def _calculate_solution_cost(self, workload: List[QueryCostInfo], baseline_costs: Dict[str, QueryCostInfo],
                                index_config: IndexConfiguration) -> float:
        """Calculate total benefit score for a configuration"""
        total_cost = 0.0
        total_improvement = 0.0

        for query_info in workload:
            if query_info.template_hash in baseline_costs:
                baseline = baseline_costs[query_info.template_hash]
                optimized_costs = self.cost_model.estimate_query_cost(query_info.query, index_config)

                # Calculate improvement metrics
                if baseline and baseline.base_cost and baseline.base_cost > 0:
                    cost_improvement = (baseline.base_cost - optimized_costs['total_cost']) / baseline.base_cost
                else:
                    cost_improvement = 0.0

                if baseline and baseline.base_time and baseline.base_time > 0:
                    time_improvement = (baseline.base_time - optimized_costs['execution_time']) / baseline.base_time
                else:
                    time_improvement = 0.0

                # Weighted improvement based on query frequency
                weighted_improvement = (cost_improvement * 0.7 + time_improvement * 0.3) * query_info.frequency
                total_improvement += weighted_improvement

        # Subtract storage and maintenance costs
        storage_penalty = index_config.storage_cost * 0.1  # 10% weight for storage
        maintenance_penalty = index_config.maintenance_cost * 0.05  # 5% weight for maintenance

        return max(total_improvement - storage_penalty - maintenance_penalty, 0.0)

    def _calculate_improvement(self, workload: List[QueryCostInfo], old_config: IndexConfiguration,
                           new_config: IndexConfiguration) -> float:
        """Calculate improvement between two configurations"""
        logger.debug(f"Calculating improvement: old_config has {len(old_config.indexes)} indexes, new_config has {len(new_config.indexes)} indexes")
        old_score = self._calculate_solution_cost(workload, self.baseline_costs, old_config)
        new_score = self._calculate_solution_cost(workload, self.baseline_costs, new_config)
        improvement = new_score - old_score
        logger.info(f"Individual index improvement: {improvement:.6f} (old: {old_score:.6f}, new: {new_score:.6f})")
        return improvement


class CostBasedIndexAdvisor:
    """Main interface for cost-based index optimization"""

    def __init__(self, db_connection_params):
        self.db_params = db_connection_params
        self.optimizer = AnytimeIndexOptimizer(db_connection_params)

    def recommend_indexes(self, workload_queries: List[str], schema_info: Dict,
                         time_limit_seconds: int = 60) -> ProgressiveResult:
        """Main entry point for cost-based index recommendation"""
        logger.info(f"Starting cost-based index optimization for {len(workload_queries)} queries")

        # Convert queries to QueryCostInfo objects
        query_workload = self._prepare_workload(workload_queries)

        # Run progressive optimization
        result = self.optimizer.optimize_progressively(query_workload, schema_info)

        return result

    def _prepare_workload(self, queries: List[str]) -> List[QueryCostInfo]:
        """Prepare workload data for optimization"""
        # Group queries by template and calculate frequency
        template_counts = defaultdict(int)
        templates = {}

        for query in queries:
            # Convert query to string for hashing (queries are dicts from JSON)
            query_str = json.dumps(query, sort_keys=True) if isinstance(query, dict) else str(query)
            template_hash = hash(query_str)
            templates[template_hash] = query
            template_counts[template_hash] += 1

        # Debug: Log the calculated frequencies
        workload = []
        for hash_val, count in template_counts.items():
            logger.info(f"Query template frequency: {count} for query: {str(templates[hash_val])[:100]}...")
            # Extract actual SQL string from query object
            query_obj = templates[hash_val]
            query_str = query_obj.get('query', str(query_obj)) if isinstance(query_obj, dict) else str(query_obj)
            workload.append(QueryCostInfo(
                query=query_str,
                template_hash=hash_val,
                frequency=count,
                base_cost=0.0,
                base_time=0.0,
                base_io=0
            ))
        return workload