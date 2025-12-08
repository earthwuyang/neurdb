"""
Hypothetical Index Analyzer for NeurDB
Integrates with hypopg extension to validate index recommendations using EXPLAIN
"""

import re
import json
import logging
import asyncio
from typing import Dict, List, Tuple, Set, Optional, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
import numpy as np
import psycopg2
from psycopg2 import sql, OperationalError, DatabaseError
import psycopg2.extras

from index_advisor import IndexCandidate, IndexType, QueryAnalysis

logger = logging.getLogger(__name__)


class HypotheticalIndexResult(Enum):
    """Result of hypothetical index analysis"""
    SUCCESS = "success"
    INDEX_EXISTS = "index_exists"
    INDEX_INVALID = "index_invalid"
    QUERY_ERROR = "query_error"
    CONNECTION_ERROR = "connection_error"


@dataclass
class HypotheticalIndexAnalysis:
    """Results from hypothetical index analysis"""
    index_candidate: IndexCandidate
    original_plan_cost: float
    hypothetical_plan_cost: float
    cost_reduction_percentage: float
    original_plan_time_ms: float
    hypothetical_plan_time_ms: float
    time_reduction_percentage: float
    plan_changes: List[str]
    index_usage_detected: bool
    analysis_status: HypotheticalIndexResult
    error_message: Optional[str] = None
    explain_analyze_output: Optional[str] = None
    hypothetical_explain_output: Optional[str] = None


@dataclass
class HypotheticalIndexConfig:
    """Configuration for hypothetical index analyzer"""
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "neurdb"
    database_user: str = "postgres"
    database_password: str = "postgres"
    connection_timeout: int = 30
    analysis_timeout: int = 60
    enable_explain_analyze: bool = False
    min_cost_reduction_threshold: float = 0.05  # 5% minimum cost reduction
    max_concurrent_analyses: int = 5
    cache_results: bool = True
    cache_ttl_hours: int = 24


class HypotheticalIndexAnalyzer:
    """
    Analyzes index candidates using PostgreSQL's hypothetical index functionality
    through the hypopg extension
    """

    def __init__(self, config: HypotheticalIndexConfig):
        """
        Initialize the hypothetical index analyzer

        Args:
            config: Database and analysis configuration
        """
        self.config = config
        self.connection_pool = []
        self.analysis_cache = {}
        self.cache_timestamps = {}

        # Initialize hypopg extension state
        self.hypopg_enabled = False
        self.active_hypothetical_indexes = set()

        logger.info("HypotheticalIndexAnalyzer initialized")

    async def initialize(self):
        """Initialize database connections and verify hypopg extension"""
        try:
            # Test database connection
            conn = await self._get_connection()

            # Verify hypopg extension is available
            await self._verify_hypopg_extension(conn)

            # Initialize hypopg
            await self._initialize_hypopg(conn)

            await conn.close()
            logger.info("HypotheticalIndexAnalyzer initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize HypotheticalIndexAnalyzer: {e}")
            raise

    async def _get_connection(self) -> psycopg2.extensions.connection:
        """Get a database connection"""
        try:
            conn = psycopg2.connect(
                host=self.config.database_host,
                port=self.config.database_port,
                database=self.config.database_name,
                user=self.config.database_user,
                password=self.config.database_password,
                connect_timeout=self.config.connection_timeout
            )
            conn.set_session(autocommit=True)
            return conn
        except OperationalError as e:
            logger.error(f"Database connection failed: {e}")
            raise

    async def _verify_hypopg_extension(self, conn):
        """Verify that hypopg extension is installed"""
        try:
            with conn.cursor() as cur:
                # Check if hypopg extension exists
                cur.execute("""
                    SELECT 1 FROM pg_extension WHERE extname = 'hypopg'
                """)

                if cur.fetchone():
                    logger.info("hypopg extension found")
                    self.hypopg_enabled = True
                else:
                    # Try to create the extension
                    logger.info("Installing hypopg extension...")
                    cur.execute("CREATE EXTENSION IF NOT EXISTS hypopg")
                    self.hypopg_enabled = True
                    logger.info("hypopg extension installed successfully")

        except DatabaseError as e:
            logger.error(f"Failed to verify/install hypopg extension: {e}")
            raise

    async def _initialize_hypopg(self, conn):
        """Initialize hypopg and verify it's working"""
        try:
            with conn.cursor() as cur:
                # Test hypopg functionality
                cur.execute("SELECT hypopg_reset()")
                cur.execute("SELECT * FROM hypopg() LIMIT 1")

                # Check hypopg settings
                cur.execute("SHOW hypopg.enabled")
                enabled = cur.fetchone()[0]

                cur.execute("SHOW hypopg.use_real_oids")
                use_real_oids = cur.fetchone()[0]

                logger.info(f"hypopg status: enabled={enabled}, use_real_oids={use_real_oids}")

        except DatabaseError as e:
            logger.error(f"Failed to initialize hypopg: {e}")
            raise

    async def analyze_index_candidate(self, candidate: IndexCandidate,
                                    query_analysis: QueryAnalysis,
                                    sample_queries: List[str] = None) -> HypotheticalIndexAnalysis:
        """
        Analyze an index candidate using hypothetical indexes

        Args:
            candidate: Index candidate to analyze
            query_analysis: Query analysis results
            sample_queries: Optional list of sample queries to test

        Returns:
            HypotheticalIndexAnalysis with detailed results
        """
        cache_key = self._generate_cache_key(candidate, query_analysis)

        # Check cache first
        if (self.config.cache_results and
            cache_key in self.analysis_cache and
            self._is_cache_valid(cache_key)):
            logger.debug(f"Using cached analysis for {candidate.table_name}.{candidate.columns}")
            return self.analysis_cache[cache_key]

        conn = None
        try:
            conn = await self._get_connection()

            # Get baseline EXPLAIN plan
            original_plan = await self._get_explain_plan(
                conn, query_analysis.template_text, analyze=self.config.enable_explain_analyze
            )

            # Create hypothetical index
            index_oid = await self._create_hypothetical_index(conn, candidate)
            if not index_oid:
                return self._create_error_result(candidate, "Failed to create hypothetical index")

            try:
                # Get EXPLAIN plan with hypothetical index
                hypothetical_plan = await self._get_explain_plan(
                    conn, query_analysis.template_text, analyze=self.config.enable_explain_analyze
                )

                # Analyze plan differences
                analysis = await self._analyze_plan_differences(
                    candidate, original_plan, hypothetical_plan, query_analysis
                )

                # Cache results
                if self.config.cache_results:
                    self.analysis_cache[cache_key] = analysis
                    self.cache_timestamps[cache_key] = datetime.now()

                return analysis

            finally:
                # Clean up hypothetical index
                await self._drop_hypothetical_index(conn, index_oid)

        except Exception as e:
            logger.error(f"Failed to analyze index candidate: {e}")
            return self._create_error_result(candidate, str(e))

        finally:
            if conn:
                await conn.close()

    async def analyze_multiple_candidates(self, candidates: List[IndexCandidate],
                                        query_analyses: List[QueryAnalysis],
                                        sample_queries: Dict[str, List[str]] = None) -> List[HypotheticalIndexAnalysis]:
        """
        Analyze multiple index candidates concurrently

        Args:
            candidates: List of index candidates to analyze
            query_analyses: Corresponding query analyses
            sample_queries: Optional sample queries for each template

        Returns:
            List of hypothetical index analyses
        """
        results = []

        # Create mapping from template hash to analysis
        analysis_map = {qa.template_hash: qa for qa in query_analyses}

        # Process candidates in batches to limit concurrency
        batch_size = self.config.max_concurrent_analyses

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]

            # Create concurrent tasks for this batch
            tasks = []
            for candidate in batch:
                # Find corresponding query analysis
                template_hash = candidate.supporting_templates[0] if candidate.supporting_templates else None
                query_analysis = analysis_map.get(template_hash)

                if query_analysis:
                    sample_queries_for_template = sample_queries.get(template_hash, []) if sample_queries else []
                    task = self.analyze_index_candidate(candidate, query_analysis, sample_queries_for_template)
                    tasks.append(task)
                else:
                    # Create error result if no query analysis found
                    error_result = self._create_error_result(
                        candidate, f"No query analysis found for template {template_hash}"
                    )
                    tasks.append(asyncio.create_task(asyncio.coroutine(lambda: error_result)()))

            # Wait for batch completion
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in batch_results:
                if isinstance(result, Exception):
                    logger.error(f"Analysis failed with exception: {result}")
                    # Create error result for failed analysis
                    error_result = self._create_error_result(
                        candidate if 'candidate' in locals() else batch[0], str(result)
                    )
                    results.append(error_result)
                else:
                    results.append(result)

            logger.info(f"Completed analysis batch {i//batch_size + 1}/{(len(candidates)-1)//batch_size + 1}")

        logger.info(f"Completed analysis of {len(results)} index candidates")
        return results

    async def _get_explain_plan(self, conn, query_text: str, analyze: bool = False) -> Dict[str, Any]:
        """Get EXPLAIN plan for a query"""
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                explain_type = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)" if analyze else "EXPLAIN (FORMAT JSON)"
                explain_query = f"{explain_type} {query_text}"

                cur.execute(explain_query)
                result = cur.fetchone()

                if result and 'QUERY PLAN' in result:
                    plan_data = result['QUERY PLAN'][0]  # PostgreSQL returns JSON as a list
                    return plan_data
                else:
                    raise ValueError("No plan data returned from EXPLAIN")

        except DatabaseError as e:
            logger.error(f"Failed to get EXPLAIN plan: {e}")
            raise

    async def _create_hypothetical_index(self, conn, candidate: IndexCandidate) -> Optional[int]:
        """Create a hypothetical index using hypopg"""
        try:
            with conn.cursor() as cur:
                # Generate CREATE INDEX statement
                create_sql = candidate.sql_statements[0] if candidate.sql_statements else None
                if not create_sql:
                    # Generate default CREATE INDEX statement
                    columns_str = ", ".join(candidate.columns)
                    create_sql = f"CREATE INDEX ON {candidate.table_name} ({columns_str})"

                # Use hypopg to create hypothetical index
                # hypopg doesn't have a direct function for this, so we need to simulate
                # the index creation by extracting index definition and using hypopg's internal functions

                # For now, we'll use a simplified approach - in a full implementation,
                # we'd need to parse the CREATE INDEX statement and use hypopg's API
                index_name = f"hypothetical_{candidate.table_name}_{'_'.join(candidate.columns)}"

                # Create the hypothetical index using hypopg's simulated approach
                # This is a simplified version - a full implementation would be more complex
                cur.execute(f"""
                    SELECT hypopg_create_index(
                        'CREATE INDEX {index_name} ON {candidate.table_name} ({", ".join(candidate.columns)})'
                    )
                """)

                result = cur.fetchone()
                if result and result[0]:
                    index_oid = result[0]
                    self.active_hypothetical_indexes.add(index_oid)
                    logger.debug(f"Created hypothetical index {index_oid} for {candidate.table_name}")
                    return index_oid
                else:
                    logger.error("Failed to create hypothetical index")
                    return None

        except DatabaseError as e:
            logger.error(f"Failed to create hypothetical index: {e}")
            return None

    async def _drop_hypothetical_index(self, conn, index_oid: int):
        """Drop a hypothetical index"""
        try:
            if index_oid in self.active_hypothetical_indexes:
                with conn.cursor() as cur:
                    # hypopg_reset() clears all hypothetical indexes
                    # For individual index removal, we'd need specific hypopg functions
                    cur.execute("SELECT hypopg_reset()")
                    self.active_hypothetical_indexes.discard(index_oid)
                    logger.debug(f"Dropped hypothetical index {index_oid}")

        except DatabaseError as e:
            logger.error(f"Failed to drop hypothetical index {index_oid}: {e}")

    async def _analyze_plan_differences(self, candidate: IndexCandidate,
                                      original_plan: Dict[str, Any],
                                      hypothetical_plan: Dict[str, Any],
                                      query_analysis: QueryAnalysis) -> HypotheticalIndexAnalysis:
        """Analyze differences between original and hypothetical execution plans"""

        # Extract cost information
        original_cost = self._extract_plan_cost(original_plan)
        hypothetical_cost = self._extract_plan_cost(hypothetical_plan)

        # Extract execution time information
        original_time = self._extract_plan_time(original_plan)
        hypothetical_time = self._extract_plan_time(hypothetical_plan)

        # Calculate improvements
        cost_reduction = 0.0
        if original_cost > 0:
            cost_reduction = (original_cost - hypothetical_cost) / original_cost

        time_reduction = 0.0
        if original_time > 0:
            time_reduction = (original_time - hypothetical_time) / original_time

        # Detect plan changes
        plan_changes = self._detect_plan_changes(original_plan, hypothetical_plan)

        # Check if index is being used
        index_usage = self._detect_index_usage(hypothetical_plan, candidate)

        # Create analysis result
        analysis = HypotheticalIndexAnalysis(
            index_candidate=candidate,
            original_plan_cost=original_cost,
            hypothetical_plan_cost=hypothetical_cost,
            cost_reduction_percentage=cost_reduction * 100,
            original_plan_time_ms=original_time,
            hypothetical_plan_time_ms=hypothetical_time,
            time_reduction_percentage=time_reduction * 100,
            plan_changes=plan_changes,
            index_usage_detected=index_usage,
            analysis_status=HypotheticalIndexResult.SUCCESS,
            explain_analyze_output=json.dumps(original_plan, indent=2),
            hypothetical_explain_output=json.dumps(hypothetical_plan, indent=2)
        )

        logger.debug(f"Index analysis for {candidate.table_name}: "
                    f"cost reduction {cost_reduction:.2%}, index used: {index_usage}")

        return analysis

    def _extract_plan_cost(self, plan: Dict[str, Any]) -> float:
        """Extract total cost from execution plan"""
        if isinstance(plan, dict) and 'Plan' in plan:
            return plan['Plan'].get('Total Cost', 0.0)
        return 0.0

    def _extract_plan_time(self, plan: Dict[str, Any]) -> float:
        """Extract execution time from plan (if ANALYZE was used)"""
        if isinstance(plan, dict):
            # Look for Execution Time in ANALYZE output
            return plan.get('Execution Time', 0.0)
        return 0.0

    def _detect_plan_changes(self, original_plan: Dict[str, Any],
                           hypothetical_plan: Dict[str, Any]) -> List[str]:
        """Detect changes between execution plans"""
        changes = []

        # Extract node types from both plans
        original_nodes = self._extract_plan_node_types(original_plan)
        hypothetical_nodes = self._extract_plan_node_types(hypothetical_plan)

        # Find differences
        original_set = set(original_nodes)
        hypothetical_set = set(hypothetical_nodes)

        # Removed nodes
        removed = original_set - hypothetical_set
        if removed:
            changes.append(f"Removed operations: {', '.join(removed)}")

        # Added nodes
        added = hypothetical_set - original_set
        if added:
            changes.append(f"Added operations: {', '.join(added)}")

        # Check for index scans
        if 'Index Scan' in hypothetical_nodes and 'Index Scan' not in original_nodes:
            changes.append("Added Index Scan operation")

        if 'Index Only Scan' in hypothetical_nodes and 'Index Only Scan' not in original_nodes:
            changes.append("Added Index Only Scan operation")

        # Check for eliminated sorts
        if 'Sort' in original_nodes and 'Sort' not in hypothetical_nodes:
            changes.append("Eliminated Sort operation (order preserved by index)")

        return changes

    def _extract_plan_node_types(self, plan: Dict[str, Any]) -> List[str]:
        """Extract all node types from execution plan"""
        node_types = []

        def extract_nodes(node):
            if isinstance(node, dict):
                if 'Node Type' in node:
                    node_types.append(node['Node Type'])

                # Recursively extract from subplans
                for key in ['Plans', 'Plan']:
                    if key in node and isinstance(node[key], list):
                        for subplan in node[key]:
                            extract_nodes(subplan)
                    elif key in node and isinstance(node[key], dict):
                        extract_nodes(node[key])

        if 'Plan' in plan:
            extract_nodes(plan['Plan'])

        return node_types

    def _detect_index_usage(self, plan: Dict[str, Any], candidate: IndexCandidate) -> bool:
        """Detect if the hypothetical index is being used in the plan"""
        plan_text = json.dumps(plan, lower=True)

        # Look for index scan operations
        if 'index scan' in plan_text or 'index only scan' in plan_text:
            # Check if our index name or columns are mentioned
            index_columns = ' '.join(candidate.columns).lower()
            table_name = candidate.table_name.lower()

            if table_name in plan_text and index_columns in plan_text:
                return True

        return False

    def _generate_cache_key(self, candidate: IndexCandidate, query_analysis: QueryAnalysis) -> str:
        """Generate cache key for analysis results"""
        columns_str = '_'.join(sorted(candidate.columns))
        return f"{candidate.table_name}_{columns_str}_{query_analysis.template_hash}"

    def _is_cache_valid(self, cache_key: str) -> bool:
        """Check if cached result is still valid"""
        if cache_key not in self.cache_timestamps:
            return False

        age = datetime.now() - self.cache_timestamps[cache_key]
        return age < timedelta(hours=self.config.cache_ttl_hours)

    def _create_error_result(self, candidate: IndexCandidate, error_message: str) -> HypotheticalIndexAnalysis:
        """Create an error analysis result"""
        return HypotheticalIndexAnalysis(
            index_candidate=candidate,
            original_plan_cost=0.0,
            hypothetical_plan_cost=0.0,
            cost_reduction_percentage=0.0,
            original_plan_time_ms=0.0,
            hypothetical_plan_time_ms=0.0,
            time_reduction_percentage=0.0,
            plan_changes=[],
            index_usage_detected=False,
            analysis_status=HypotheticalIndexResult.QUERY_ERROR,
            error_message=error_message
        )

    async def get_index_recommendations_with_analysis(self, candidates: List[IndexCandidate],
                                                   query_analyses: List[QueryAnalysis],
                                                   sample_queries: Dict[str, List[str]] = None) -> List[HypotheticalIndexAnalysis]:
        """
        Get index recommendations with hypothetical analysis validation

        Args:
            candidates: List of index candidates to validate
            query_analyses: Corresponding query analyses
            sample_queries: Optional sample queries for testing

        Returns:
            List of analyses sorted by actual measured benefit
        """
        logger.info(f"Starting hypothetical analysis for {len(candidates)} candidates")

        # Analyze all candidates
        analyses = await self.analyze_multiple_candidates(candidates, query_analyses, sample_queries)

        # Filter successful analyses with meaningful improvements
        valid_analyses = [
            analysis for analysis in analyses
            if (analysis.analysis_status == HypotheticalIndexResult.SUCCESS and
                analysis.index_usage_detected and
                analysis.cost_reduction_percentage >= self.config.min_cost_reduction_threshold * 100)
        ]

        # Sort by actual cost reduction (descending)
        valid_analyses.sort(key=lambda x: x.cost_reduction_percentage, reverse=True)

        logger.info(f"Found {len(valid_analyses)} validated index recommendations")

        return valid_analyses

    async def cleanup(self):
        """Clean up resources"""
        # Clear cache
        self.analysis_cache.clear()
        self.cache_timestamps.clear()

        # Close any open connections
        for conn in self.connection_pool:
            try:
                await conn.close()
            except:
                pass

        self.connection_pool.clear()
        logger.info("HypotheticalIndexAnalyzer cleanup complete")