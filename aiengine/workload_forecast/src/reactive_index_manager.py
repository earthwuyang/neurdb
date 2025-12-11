#!/usr/bin/env python3
"""
Reactive Index Management Strategy
Implements real-time index management for incoming queries with budget-aware eviction
"""

import asyncio
import json
import logging
import time
import hashlib
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set, Union
from dataclasses import dataclass, asdict
from enum import Enum
import numpy as np

def safe_asdict(obj):
    """Convert dataclass to dict, handling Enums properly"""
    if hasattr(obj, '__dataclass_fields__'):
        result = {}
        for key, value in asdict(obj).items():
            if isinstance(value, Enum):
                result[key] = value.value
            else:
                result[key] = value
        return result
    return obj
import psycopg2
from psycopg2.extras import RealDictCursor
import sqlparse
from sqlparse.sql import Identifier, Comparison, Function

from cost_based_index_advisor import CostBasedIndexAdvisor, IndexCandidate
from hypothetical_analyzer import HypotheticalIndexAnalyzer

logger = logging.getLogger(__name__)


class EvictionPolicy(Enum):
    """Eviction policies for reactive index management"""
    LRU = "lru"                    # Least Recently Used
    LFU = "lfu"                    # Least Frequently Used
    BENEFIT_BASED = "benefit_based"  # Lowest benefit/cost ratio
    COST_BASED = "cost_based"        # Highest maintenance cost
    SIZE_BASED = "size_based"        # Largest index first


@dataclass
class IndexBenefit:
    """Represents benefit calculation for an index"""
    index_name: str
    table_name: str
    columns: List[str]
    benefit_score: float
    estimated_cost_savings: float
    query_count: int
    last_accessed: datetime
    creation_time: datetime
    access_frequency: float
    storage_cost_mb: float


@dataclass
class QueryAnalysis:
    """Result of query analysis for index recommendation"""
    query_hash: str
    query_text: str
    affected_tables: List[str]
    where_columns: List[str]
    join_columns: List[str]
    order_by_columns: List[str]
    group_by_columns: List[str]
    estimated_rows: int
    current_cost: float
    recommended_indexes: List[IndexCandidate]


@dataclass
class ReactiveConfig:
    """Configuration for reactive index management"""
    # Database connection
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "neurdb"
    database_user: str = "neurdb"
    database_password: str = "postgres"

    # Index management settings
    storage_budget_mb: float = 1000.0
    min_benefit_threshold: float = 0.1
    eviction_policy: EvictionPolicy = EvictionPolicy.BENEFIT_BASED
    max_indexes_total: int = 50
    max_indexes_per_table: int = 5

    # Recommendation settings
    enable_auto_creation: bool = True
    cache_recommendations: bool = True
    recommendation_ttl_hours: int = 1
    max_recommendations_per_query: int = 3

    # Analysis settings
    use_hypopg: bool = True
    consider_existing_indexes: bool = True
    analyze_joins: bool = True
    analyze_aggregations: bool = True

    # Performance settings
    index_creation_timeout_seconds: int = 30
    max_analysis_time_seconds: int = 5


class IndexCache:
    """Cache for index recommendations and benefits"""

    def __init__(self, ttl_hours: int = 1):
        self.cache: Dict[str, Dict] = {}
        self.ttl_seconds = ttl_hours * 3600
        self.last_cleanup = datetime.now()

    def get(self, key: str) -> Optional[Dict]:
        """Get cached value if not expired"""
        if key not in self.cache:
            return None

        entry = self.cache[key]
        if datetime.now() - entry['timestamp'] > timedelta(seconds=self.ttl_seconds):
            del self.cache[key]
            return None

        return entry['value']

    def set(self, key: str, value: Dict):
        """Set cached value with timestamp"""
        self.cache[key] = {
            'value': value,
            'timestamp': datetime.now()
        }

    def cleanup_expired(self):
        """Remove expired entries"""
        if datetime.now() - self.last_cleanup < timedelta(hours=1):
            return

        expired_keys = []
        for key, entry in self.cache.items():
            if datetime.now() - entry['timestamp'] > timedelta(seconds=self.ttl_seconds):
                expired_keys.append(key)

        for key in expired_keys:
            del self.cache[key]

        self.last_cleanup = datetime.now()


class ReactiveIndexManager:
    """Implements reactive index management with real-time optimization"""

    def __init__(self, config: ReactiveConfig):
        self.config = config
        self.db_params = {
            'host': config.database_host,
            'port': config.database_port,
            'database': config.database_name,
            'user': config.database_user,
            'password': config.database_password
        }

        # Initialize components
        self.index_advisor = CostBasedIndexAdvisor(self.db_params)
        self.hypothetical_analyzer = HypotheticalIndexAnalyzer(self.db_params)
        self.recommendation_cache = IndexCache(config.recommendation_ttl_hours)

        # State tracking
        self.current_indexes: Dict[str, IndexBenefit] = {}
        self.query_history: List[Dict] = []
        self.index_usage_stats: Dict[str, Dict] = {}

        logger.info("Reactive Index Manager initialized")

    async def process_query(self, query_text: str, force_analysis: bool = False) -> Dict:
        """
        Process a single query and manage indexes accordingly
        """
        start_time = time.time()
        query_hash = self._hash_query(query_text)

        try:
            # 1. Check cache first
            if not force_analysis and self.config.cache_recommendations:
                cached_result = self.recommendation_cache.get(query_hash)
                if cached_result:
                    logger.debug(f"Using cached recommendation for query {query_hash[:8]}...")
                    return cached_result

            # 2. Analyze the query
            analysis = await self._analyze_query(query_text, query_hash)

            # 3. Get recommendations
            recommendations = await self._get_recommendations(analysis)

            logger.debug(f"recommendations: {recommendations}")

            # 4. Check if we should create any indexes
            created_indexes = []
            if self.config.enable_auto_creation and recommendations:
                created_indexes = await self._manage_indexes(recommendations, analysis)

            # 5. Update query history
            self._update_query_history(query_text, analysis, recommendations, created_indexes)

            # 6. Cache the result
            if self.config.cache_recommendations:
                self.recommendation_cache.set(query_hash, {
                    'analysis': safe_asdict(analysis),
                    'recommendations': [safe_asdict(r) for r in recommendations],
                    'created_indexes': created_indexes,
                    'processing_time': time.time() - start_time
                })

            result = {
                'query_hash': query_hash,
                'analysis': safe_asdict(analysis),
                'recommendations': [safe_asdict(r) for r in recommendations],
                'created_indexes': created_indexes,
                'processing_time': time.time() - start_time,
                'current_storage_usage': await self._get_storage_usage()
            }

            logger.info(f"Processed query {query_hash[:8]} in {result['processing_time']:.3f}s")
            return result

        except Exception as e:
            logger.error(f"Error processing query {query_hash[:8]}: {e}")
            return {
                'query_hash': query_hash,
                'error': str(e),
                'processing_time': time.time() - start_time
            }

    async def _analyze_query(self, query_text: str, query_hash: str) -> QueryAnalysis:
        """Analyze query for index opportunities"""
        try:
            # Parse SQL query
            parsed = sqlparse.parse(query_text)[0]

            # Extract table references
            tables = self._extract_tables(parsed)

            # Extract column usage patterns
            where_columns = self._extract_where_columns(parsed)
            join_columns = self._extract_join_columns(parsed)
            order_by_columns = self._extract_order_by_columns(parsed)
            group_by_columns = self._extract_group_by_columns(parsed)

            # Get current cost using HypoPG
            current_cost = await self._get_query_cost(query_text) if self.config.use_hypopg else 0.0

            # Estimate row count
            estimated_rows = await self._estimate_query_rows(query_text, tables)

            analysis = QueryAnalysis(
                query_hash=query_hash,
                query_text=query_text,
                affected_tables=tables,
                where_columns=where_columns,
                join_columns=join_columns,
                order_by_columns=order_by_columns,
                group_by_columns=group_by_columns,
                estimated_rows=estimated_rows,
                current_cost=current_cost,
                recommended_indexes=[]  # Will be filled later
            )

            return analysis

        except Exception as e:
            logger.error(f"Error analyzing query: {e}")
            # Return basic analysis
            return QueryAnalysis(
                query_hash=query_hash,
                query_text=query_text,
                affected_tables=[],
                where_columns=[],
                join_columns=[],
                order_by_columns=[],
                group_by_columns=[],
                estimated_rows=0,
                current_cost=0.0,
                recommended_indexes=[]
            )

    def _extract_tables(self, parsed_query) -> List[str]:
        """Extract table names from parsed query"""
        tables = []

        def extract_from_tokens(token_list):
            for token in token_list:
                if hasattr(token, 'tokens'):
                    extract_from_tokens(token.tokens)
                elif hasattr(token, 'value') and hasattr(token, 'ttype'):
                    # Check if this token is a table reference
                    if (token.ttype in sqlparse.tokens.Name and
                        token.value.upper() not in ['SELECT', 'FROM', 'WHERE', 'JOIN', 'INNER',
                                                   'LEFT', 'RIGHT', 'OUTER', 'FULL', 'ON',
                                                   'GROUP', 'BY', 'ORDER', 'HAVING']):
                        tables.append(token.value)

        extract_from_tokens(parsed_query.tokens)
        return list(set(tables))  # Remove duplicates

    def _extract_where_columns(self, parsed_query) -> List[str]:
        """Extract columns from WHERE clause"""
        columns = []

        # Simple extraction - in practice, this would be more sophisticated
        query_str = str(parsed_query)
        where_match = sqlparse.parse(query_str)[0]

        def find_columns_in_token(token):
            if hasattr(token, 'tokens'):
                for subtoken in token.tokens:
                    find_columns_in_token(subtoken)
            elif hasattr(token, 'value'):
                if hasattr(token, 'ttype') and token.ttype == sqlparse.tokens.Name:
                    if token.value not in ['AND', 'OR', 'NOT', 'IN', 'LIKE', 'BETWEEN']:
                        columns.append(token.value)

        # Find WHERE clause and extract columns
        for token in where_match.tokens:
            if hasattr(token, 'value') and token.value.upper() == 'WHERE':
                find_columns_in_token(token)
                break

        return list(set(columns))

    def _extract_join_columns(self, parsed_query) -> List[str]:
        """Extract columns from JOIN conditions"""
        columns = []
        query_str = str(parsed_query)

        # Look for ON clauses in joins
        join_pattern = r'ON\s+(\w+\.\w+)'
        matches = [match.split('.')[1] for match in re.findall(join_pattern, query_str, re.IGNORECASE)]
        columns.extend(matches)

        return list(set(columns))

    def _extract_order_by_columns(self, parsed_query) -> List[str]:
        """Extract columns from ORDER BY clause"""
        columns = []
        query_str = str(parsed_query)

        # Look for ORDER BY clause
        order_match = re.search(r'ORDER BY\s+(.+?)(?:\s+LIMIT|\s+OFFSET|$)', query_str, re.IGNORECASE)
        if order_match:
            order_clause = order_match.group(1)
            # Extract column names (handling ASC/DESC)
            for item in order_clause.split(','):
                column = item.strip().split()[0]  # Take first word
                if '.' in column:
                    column = column.split('.')[1]  # Remove table prefix
                columns.append(column)

        return list(set(columns))

    def _extract_group_by_columns(self, parsed_query) -> List[str]:
        """Extract columns from GROUP BY clause"""
        columns = []
        query_str = str(parsed_query)

        # Look for GROUP BY clause
        group_match = re.search(r'GROUP BY\s+(.+?)(?:\s+HAVING|\s+ORDER|\s+LIMIT|$)', query_str, re.IGNORECASE)
        if group_match:
            group_clause = group_match.group(1)
            for item in group_clause.split(','):
                column = item.strip().split()[0]
                if '.' in column:
                    column = column.split('.')[1]
                columns.append(column)

        return list(set(columns))

    async def _get_query_cost(self, query_text: str) -> float:
        """Get query execution cost using EXPLAIN"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                cursor.execute(f"EXPLAIN (FORMAT JSON) {query_text}")
                result = cursor.fetchone()
                if result:
                    plan = result[0]
                    return plan[0]['Plan']['Total Cost']
                return 0.0
        except Exception as e:
            logger.error(f"Error getting query cost: {e}")
            return 0.0
        finally:
            if 'conn' in locals():
                conn.close()

    async def _estimate_query_rows(self, query_text: str, tables: List[str]) -> int:
        """Estimate number of rows a query will return"""
        try:
            if not tables:
                return 1000  # Default estimate

            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Get table statistics
                total_rows = 0
                for table in tables:
                    try:
                        cursor.execute(f"SELECT reltuples FROM pg_class WHERE relname = '{table}'")
                        result = cursor.fetchone()
                        if result:
                            total_rows += result[0]
                    except:
                        continue

                # Apply selectivity factors based on query characteristics
                selectivity = 0.1  # Default 10%
                if 'WHERE' in query_text.upper():
                    selectivity = 0.05  # WHERE clause reduces results
                if 'JOIN' in query_text.upper():
                    selectivity *= 0.1  # JOINs often reduce results

                estimated_rows = int(total_rows * max(0.001, min(1.0, selectivity)))
                return max(1, min(estimated_rows, 10000000))  # Reasonable bounds

        except Exception as e:
            logger.error(f"Error estimating query rows: {e}")
            return 1000
        finally:
            if 'conn' in locals():
                conn.close()

    async def _get_recommendations(self, analysis: QueryAnalysis) -> List[IndexCandidate]:
        """Get index recommendations for the query analysis"""
        try:
            recommendations = []

            # Use existing cost-based advisor
            workload_queries = [analysis.query_text]
            schema_info = {}  # Will be populated by the advisor

            # Get index recommendations
            recommendation_result = self.index_advisor.recommend_indexes(
                workload_queries=workload_queries,
                schema_info=schema_info,
                time_limit_seconds=self.config.max_analysis_time_seconds,
                reactive = True
            )

            # Extract recommended indexes from ProgressiveResult
            index_recommendations = recommendation_result.best_solution.indexes

            # Filter and format recommendations
            for rec in index_recommendations:
                if (rec.benefit_score >= self.config.min_benefit_threshold and
                    len(recommendations) < self.config.max_recommendations_per_query):

                    recommendations.append(IndexCandidate(
                        table_name=rec.table_name,
                        columns=rec.columns,
                        index_type=rec.index_type,
                        estimated_size_mb=rec.estimated_size_mb,
                        creation_cost=rec.creation_cost,
                        benefit_score=rec.benefit_score
                    ))

            return recommendations

        except Exception as e:
            logger.error(f"Error getting recommendations: {e}")
            return []

    async def _manage_indexes(self, recommendations: List[IndexCandidate], analysis: QueryAnalysis) -> List[Dict]:
        """Manage indexes based on recommendations and budget constraints"""
        created_indexes = []

        try:
            # Sort recommendations by benefit score
            recommendations.sort(key=lambda x: x.benefit_score, reverse=True)

            # Check current storage usage
            storage_usage = await self._get_storage_usage()
            available_budget = self.config.storage_budget_mb - storage_usage['current_usage_mb']

            for recommendation in recommendations:
                # Check if we have budget
                if recommendation.estimated_size_mb <= available_budget:
                    # Check if index already exists
                    existing_index = await self._find_existing_index(recommendation)

                    if not existing_index:
                        # Create the index
                        success = await self._create_index(recommendation)
                        if success:
                            created_indexes.append({
                                'table_name': recommendation.table_name,
                                'columns': recommendation.columns,
                                'index_type': recommendation.index_type.value,
                                'estimated_size_mb': recommendation.estimated_size_mb,
                                'benefit_score': recommendation.benefit_score
                            })
                            available_budget -= recommendation.estimated_size_mb
                        else:
                            # Try eviction and retry
                            if await self._evict_and_create(recommendation, recommendation.estimated_size_mb):
                                created_indexes.append({
                                    'table_name': recommendation.table_name,
                                    'columns': recommendation.columns,
                                    'index_type': recommendation.index_type.value,
                                    'estimated_size_mb': recommendation.estimated_size_mb,
                                    'benefit_score': recommendation.benefit_score,
                                    'eviction_required': True
                                })
                    else:
                        logger.debug(f"Index already exists for {recommendation.table_name} on {recommendation.columns}")
                else:
                    # Not enough budget, try eviction
                    if await self._evict_and_create(recommendation, recommendation.estimated_size_mb):
                        created_indexes.append({
                            'table_name': recommendation.table_name,
                            'columns': recommendation.columns,
                            'index_type': recommendation.index_type.value,
                            'estimated_size_mb': recommendation.estimated_size_mb,
                            'benefit_score': recommendation.benefit_score,
                            'eviction_required': True
                        })
                    else:
                        logger.warning(f"Cannot create index - insufficient budget and eviction failed")
                        break

            return created_indexes

        except Exception as e:
            logger.error(f"Error managing indexes: {e}")
            return created_indexes

    async def _find_existing_index(self, recommendation: IndexCandidate) -> Optional[str]:
        """Check if an equivalent index already exists"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Check for indexes on the same table and columns
                columns_str = ', '.join(sorted(recommendation.columns))

                cursor.execute("""
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE tablename = %s
                    AND indexdef LIKE %s
                """, (recommendation.table_name, f'%{columns_str}%'))

                result = cursor.fetchone()
                if result:
                    logger.debug(f"Found existing index: {result[0]}")
                    return result[0]

                return None

        except Exception as e:
            logger.error(f"Error checking existing index: {e}")
            return None
        finally:
            if 'conn' in locals():
                conn.close()

    async def _create_index(self, recommendation: IndexCandidate) -> bool:
        """Create a new index"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Enable auto-index creation
                cursor.execute("SET nr_enable_auto_index_creation = true;")

                # Generate index name
                timestamp = int(time.time())
                columns_str = '_'.join(recommendation.columns)
                index_name = f"idx_reactive_{recommendation.table_name}_{columns_str}_{timestamp}"

                # Create index using budget-aware function
                cursor.execute(
                    "SELECT nr_create_index_if_budget_allows(%s, %s, %s, %s)",
                    (
                        index_name,
                        recommendation.table_name,
                        recommendation.columns,
                        recommendation.index_type.value
                    )
                )
                result = cursor.fetchone()
                success = bool(result[0]) if result else False

                conn.commit()

                if success:
                    logger.info(f"Created reactive index: {index_name}")

                    # Update current indexes tracking
                    self.current_indexes[index_name] = IndexBenefit(
                        index_name=index_name,
                        table_name=recommendation.table_name,
                        columns=recommendation.columns,
                        benefit_score=recommendation.benefit_score,
                        estimated_cost_savings=0.0,  # Will be calculated later
                        query_count=1,
                        last_accessed=datetime.now(),
                        creation_time=datetime.now(),
                        access_frequency=1.0,
                        storage_cost_mb=recommendation.estimated_size_mb
                    )
                    return True
                else:
                    logger.warning(f"Failed to create index: {index_name}")
                    return False

        except Exception as e:
            logger.error(f"Error creating index {index_name}: {e}")
            return False
        finally:
            if 'conn' in locals():
                conn.close()

    async def _evict_and_create(self, new_index: IndexCandidate, required_space: float) -> bool:
        """Evict least useful index and create new one"""
        try:
            # Find index to evict based on policy
            index_to_evict = await self._find_index_to_evict(required_space)

            if not index_to_evict:
                logger.warning("No suitable index found for eviction")
                return False

            # Evict the index
            success = await self._drop_index(index_to_evict)
            if not success:
                logger.error(f"Failed to evict index {index_to_evict}")
                return False

            # Create the new index
            success = await self._create_index(new_index)
            if not success:
                logger.error(f"Failed to create new index after eviction")
                return False

            logger.info(f"Successfully evicted {index_to_evict} and created new index")
            return True

        except Exception as e:
            logger.error(f"Error in eviction and creation process: {e}")
            return False

    async def _find_index_to_evict(self, required_space: float) -> Optional[str]:
        """Find index to evict based on configured policy"""
        try:
            if not self.current_indexes:
                return None

            # Get current usage statistics for indexes
            await self._update_index_usage_stats()

            # Apply eviction policy
            if self.config.eviction_policy == EvictionPolicy.LRU:
                return min(self.current_indexes.keys(),
                          key=lambda k: self.current_indexes[k].last_accessed)

            elif self.config.eviction_policy == EvictionPolicy.LFU:
                return min(self.current_indexes.keys(),
                          key=lambda k: self.current_indexes[k].access_frequency)

            elif self.config.eviction_policy == EvictionPolicy.BENEFIT_BASED:
                # Find index with lowest benefit/cost ratio
                return min(self.current_indexes.keys(),
                          key=lambda k: self.current_indexes[k].benefit_score /
                                   max(0.1, self.current_indexes[k].storage_cost_mb))

            elif self.config.eviction_policy == EvictionPolicy.SIZE_BASED:
                # Find largest index that can free enough space
                suitable_indexes = [k for k, v in self.current_indexes.items()
                                  if v.storage_cost_mb >= required_space]
                if suitable_indexes:
                    return max(suitable_indexes,
                              key=lambda k: self.current_indexes[k].storage_cost_mb)

            elif self.config.eviction_policy == EvictionPolicy.COST_BASED:
                # Find index with highest maintenance cost
                return max(self.current_indexes.keys(),
                          key=lambda k: self.current_indexes[k].storage_cost_mb)

            return None

        except Exception as e:
            logger.error(f"Error finding index to evict: {e}")
            return None

    async def _drop_index(self, index_name: str) -> bool:
        """Drop an index"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                cursor.execute(f"DROP INDEX IF EXISTS {index_name}")
                conn.commit()

                # Remove from tracking
                if index_name in self.current_indexes:
                    del self.current_indexes[index_name]

                logger.info(f"Dropped index: {index_name}")
                return True

        except Exception as e:
            logger.error(f"Error dropping index {index_name}: {e}")
            return False
        finally:
            if 'conn' in locals():
                conn.close()

    async def _update_index_usage_stats(self):
        """Update index usage statistics"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute("""
                    SELECT
                        indexname,
                        idx_scan,
                        idx_tup_read,
                        idx_tup_fetch
                    FROM pg_stat_user_indexes
                    WHERE indexname LIKE 'idx_reactive_%'
                """)

                current_time = datetime.now()
                for row in cursor.fetchall():
                    index_name = row['indexname']

                    if index_name in self.current_indexes:
                        # Update statistics
                        index = self.current_indexes[index_name]
                        index.query_count += row['idx_scan']
                        index.last_accessed = current_time
                        index.access_frequency = index.query_count / max(1,
                            (current_time - index.creation_time).total_seconds() / 3600)

        except Exception as e:
            logger.error(f"Error updating index usage stats: {e}")
        finally:
            if 'conn' in locals():
                conn.close()

    def _update_query_history(self, query_text: str, analysis: QueryAnalysis,
                             recommendations: List[IndexCandidate], created_indexes: List[Dict]):
        """Update query history for tracking"""
        try:
            self.query_history.append({
                'timestamp': datetime.now(),
                'query_hash': analysis.query_hash,
                'query_text': query_text,
                'affected_tables': analysis.affected_tables,
                'recommendations': [asdict(r) for r in recommendations],
                'created_indexes': created_indexes,
                'processing_time': analysis.current_cost
            })

            # Keep only recent history (last 1000 queries)
            if len(self.query_history) > 1000:
                self.query_history = self.query_history[-1000:]

        except Exception as e:
            logger.error(f"Error updating query history: {e}")

    def _hash_query(self, query_text: str) -> str:
        """Generate hash for query text"""
        return hashlib.md5(query_text.encode('utf-8')).hexdigest()

    async def _get_storage_usage(self) -> Dict:
        """Get current storage usage statistics"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Use storage budget provided by caller (already computed in extension)
                budget_mb = float(self.config.storage_budget_mb)

                # Get current index storage using standard PostgreSQL system catalogs
                cursor.execute("""
                    SELECT
                        COALESCE(SUM(pg_relation_size(indexrelid)) / (1024*1024.0), 0.0) as current_usage_mb,
                        COUNT(*) as index_count
                    FROM pg_stat_user_indexes
                """)
                result = cursor.fetchone()

                current_usage_mb = float(result[0]) if result and result[0] else 0.0
                index_count = int(result[1]) if result and result[1] else 0

                return {
                    'current_usage_mb': current_usage_mb,
                    'budget_mb': budget_mb,
                    'available_mb': budget_mb - current_usage_mb,
                    'index_count': index_count
                }

        except Exception as e:
            logger.error(f"Error getting storage usage: {e}")
            return {
                'current_usage_mb': 0.0,
                'budget_mb': float(self.config.storage_budget_mb),
                'available_mb': float(self.config.storage_budget_mb),
                'index_count': 0
            }
        finally:
            if 'conn' in locals():
                conn.close()

    def _get_storage_usage_sync(self) -> Dict:
        """Get current storage usage statistics (sync version for get_status)"""
        try:
            conn = psycopg2.connect(**self.db_params)
            with conn.cursor() as cursor:
                # Get storage budget from database configuration
                cursor.execute("SHOW nr_max_index_storage_mb")
                budget_result = cursor.fetchone()
                budget_mb = float(budget_result[0]) if budget_result and budget_result[0] else float(self.config.storage_budget_mb)

                # Get current index storage using standard PostgreSQL system catalogs
                cursor.execute("""
                    SELECT
                        COALESCE(SUM(pg_relation_size(indexrelid)) / (1024*1024.0), 0.0) as current_usage_mb,
                        COUNT(*) as index_count
                    FROM pg_stat_user_indexes
                """)
                result = cursor.fetchone()

                current_usage_mb = float(result[0]) if result and result[0] else 0.0
                index_count = result[1] if result and result[1] else 0

                return {
                    'current_usage_mb': current_usage_mb,
                    'budget_mb': budget_mb,
                    'available_mb': budget_mb - current_usage_mb,
                    'index_count': index_count
                }

        except Exception as e:
            logger.error(f"Error getting storage usage: {e}")
            return {
                'current_usage_mb': 0.0,
                'budget_mb': float(self.config.storage_budget_mb),
                'available_mb': float(self.config.storage_budget_mb),
                'index_count': 0
            }
        finally:
            if 'conn' in locals():
                conn.close()

    def get_status(self) -> Dict:
        """Get current status of reactive index management"""
        self.recommendation_cache.cleanup_expired()

        return {
            'strategy': 'reactive',
            'current_indexes_count': len(self.current_indexes),
            'query_history_size': len(self.query_history),
            'cache_size': len(self.recommendation_cache.cache),
            'storage_usage': self._get_storage_usage_sync(),
            'config': {
                'storage_budget_mb': self.config.storage_budget_mb,
                'min_benefit_threshold': self.config.min_benefit_threshold,
                'eviction_policy': self.config.eviction_policy.value,
                'max_indexes_total': self.config.max_indexes_total
            }
        }

    def get_index_recommendations(self) -> List[Dict]:
        """Get current index recommendations and status"""
        recommendations = []

        # Include current indexes
        for index_name, index_info in self.current_indexes.items():
            recommendations.append({
                'index_name': index_name,
                'table_name': index_info.table_name,
                'columns': index_info.columns,
                'benefit_score': index_info.benefit_score,
                'storage_cost_mb': index_info.storage_cost_mb,
                'access_frequency': index_info.access_frequency,
                'last_accessed': index_info.last_accessed.isoformat(),
                'status': 'active',
                'recommendation_type': 'current_index'
            })

        # Include recent recommendations from cache
        for cache_key, cache_entry in self.recommendation_cache.cache.items():
            if 'recommendations' in cache_entry['value']:
                for rec in cache_entry['value']['recommendations']:
                    if not any(r['index_name'] == rec['index_name'] for r in recommendations):
                        recommendations.append({
                            **rec,
                            'status': 'recommended',
                            'recommendation_type': 'reactive'
                        })

        return recommendations
