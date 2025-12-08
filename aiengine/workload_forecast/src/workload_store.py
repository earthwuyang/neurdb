#!/usr/bin/env python3
"""
Workload Store Database Manager
Handles persistent storage of clustering data and metrics
"""

import logging
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import SimpleConnectionPool
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from contextlib import contextmanager
import json

logger = logging.getLogger(__name__)

class WorkloadStore:
    """
    Database manager for workload clustering data
    Handles PostgreSQL operations for storing templates, clusters, and time series
    """

    def __init__(self,
                 host: str = 'localhost',
                 port: int = 5432,
                 database: str = 'neurdb',
                 user: str = 'neurdb',
                 password: str = '',
                 min_connections: int = 1,
                 max_connections: int = 10):
        """
        Initialize workload store

        Args:
            host: PostgreSQL host
            port: PostgreSQL port
            database: Database name
            user: Database user
            password: Database password
            min_connections: Minimum connection pool size
            max_connections: Maximum connection pool size
        """
        self.connection_params = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password,
            'cursor_factory': RealDictCursor
        }

        self.min_connections = min_connections
        self.max_connections = max_connections
        self.pool = None

        # Initialize connection pool
        self._initialize_pool()

    def _initialize_pool(self):
        """Initialize database connection pool"""
        try:
            self.pool = SimpleConnectionPool(
                minconn=self.min_connections,
                maxconn=self.max_connections,
                **self.connection_params
            )
            logger.info(f"Initialized database connection pool: {self.min_connections}-{self.max_connections} connections")
        except Exception as e:
            logger.error(f"Failed to initialize database connection pool: {e}")
            raise

    @contextmanager
    def get_connection(self):
        """Get database connection from pool"""
        if not self.pool:
            raise RuntimeError("Database connection pool not initialized")

        conn = None
        try:
            conn = self.pool.getconn()
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database operation failed: {e}")
            raise
        finally:
            if conn:
                self.pool.putconn(conn)

    def initialize_schema(self):
        """Initialize database schema"""
        schema_file = 'sql/create_metrics_schema.sql'
        try:
            with open(schema_file, 'r') as f:
                schema_sql = f.read()

            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(schema_sql)
                conn.commit()

            logger.info("Database schema initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database schema: {e}")
            raise

    def store_query_template(self,
                           template_hash: str,
                           template_text: str,
                           logical_features: Dict = None) -> bool:
        """
        Store or update query template

        Args:
            template_hash: MD5 hash of template
            template_text: Normalized template text
            logical_features: Dictionary of logical features

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # Extract features from logical_features or template
                    query_type = logical_features.get('query_type', 'SELECT') if logical_features else 'SELECT'
                    complexity_score = logical_features.get('complexity_score', 0) if logical_features else 0
                    has_aggregation = logical_features.get('has_aggregation', False) if logical_features else False
                    has_subquery = logical_features.get('has_subquery', False) if logical_features else False
                    has_cte = logical_features.get('has_cte', False) if logical_features else False
                    tables_used = logical_features.get('tables', []) if logical_features else []
                    columns_accessed = logical_features.get('columns', []) if logical_features else []
                    join_types = logical_features.get('join_types', []) if logical_features else []

                    # UPSERT operation
                    cursor.execute("""
                        INSERT INTO neurdb_metrics.query_templates (
                            template_hash, template_text, query_type, complexity_score,
                            has_aggregation, has_subquery, has_cte,
                            tables_used, columns_accessed, join_types, logical_features
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (template_hash) DO UPDATE SET
                            template_text = EXCLUDED.template_text,
                            last_seen = NOW(),
                            total_executions = CASE
                                WHEN EXCLUDED.template_text = query_templates.template_text
                                THEN query_templates.total_executions
                                ELSE query_templates.total_executions + 1
                            END,
                            updated_at = NOW()
                    """, (
                        template_hash, template_text, query_type, complexity_score,
                        has_aggregation, has_subquery, has_cte,
                        tables_used, columns_accessed, join_types, Json(logical_features or {})
                    ))

                conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to store query template {template_hash}: {e}")
            return False

    def store_workload_timeseries(self,
                                template_hash: str,
                                timestamp: datetime,
                                execution_count: int,
                                duration_ms: float = None) -> bool:
        """
        Store workload time series data point

        Args:
            template_hash: Template hash
            timestamp: Timestamp of the data point
            execution_count: Number of executions in this time window
            duration_ms: Average duration in milliseconds

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO neurdb_metrics.workload_timeseries (
                            template_hash, timestamp, execution_count,
                            total_duration_ms, avg_duration_ms
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (template_hash, timestamp) DO UPDATE SET
                            execution_count = EXCLUDED.execution_count + workload_timeseries.execution_count,
                            total_duration_ms = EXCLUDED.total_duration_ms + EXCLUDED.total_duration_ms,
                            avg_duration_ms = (EXCLUDED.total_duration_ms + workload_timeseries.total_duration_ms) /
                                             GREATEST(EXCLUDED.execution_count + workload_timeseries.execution_count, 1)
                    """, (
                        template_hash, timestamp, execution_count,
                        duration_ms * execution_count if duration_ms else None,
                        duration_ms
                    ))

                conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to store workload timeseries for {template_hash}: {e}")
            return False

    def create_clustering_session(self,
                                 algorithm: str,
                                 parameters: Dict,
                                 input_templates_count: int) -> int:
        """
        Create a clustering session record

        Args:
            algorithm: Clustering algorithm used
            parameters: Algorithm parameters
            input_templates_count: Number of input templates

        Returns:
            Session ID
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO neurdb_metrics.clustering_sessions (
                            clustering_algorithm, parameters, input_templates_count,
                            status, created_at
                        ) VALUES (%s, %s, %s, %s, NOW())
                        RETURNING session_id
                    """, (algorithm, Json(parameters), input_templates_count, 'RUNNING'))

                    session_id = cursor.fetchone['session_id']
                conn.commit()

            logger.info(f"Created clustering session {session_id}")
            return session_id

        except Exception as e:
            logger.error(f"Failed to create clustering session: {e}")
            raise

    def complete_clustering_session(self,
                                   session_id: int,
                                   output_clusters_count: int,
                                   unassigned_count: int,
                                   merged_count: int,
                                   new_count: int,
                                   duration_ms: int,
                                   quality_metrics: Dict = None) -> bool:
        """
        Complete a clustering session with results

        Args:
            session_id: Session ID
            output_clusters_count: Number of output clusters
            unassigned_count: Number of unassigned templates
            merged_count: Number of merged clusters
            new_count: Number of new clusters
            duration_ms: Session duration in milliseconds
            quality_metrics: Quality metrics

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE neurdb_metrics.clustering_sessions
                        SET
                            output_clusters_count = %s,
                            unassigned_templates_count = %s,
                            merged_clusters_count = %s,
                            new_clusters_count = %s,
                            session_duration_ms = %s,
                            quality_metrics = %s,
                            status = %s,
                            completed_at = NOW()
                        WHERE session_id = %s
                    """, (
                        output_clusters_count, unassigned_count, merged_count,
                        new_count, duration_ms, Json(quality_metrics or {}),
                        'COMPLETED', session_id
                    ))

                conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to complete clustering session {session_id}: {e}")
            return False

    def store_cluster(self,
                      cluster_id: int,
                      cluster_name: str = None,
                      similarity_threshold: float = 0.8,
                      min_cluster_size: int = 3,
                      clustering_algorithm: str = 'ONLINE_COSINE',
                      clustering_parameters: Dict = None,
                      created_from_template: str = None) -> bool:
        """
        Store cluster information

        Args:
            cluster_id: Cluster ID
            cluster_name: Optional cluster name
            similarity_threshold: Similarity threshold used
            min_cluster_size: Minimum cluster size
            clustering_algorithm: Algorithm used
            clustering_parameters: Algorithm parameters
            created_from_template: Template hash that created this cluster

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO neurdb_metrics.template_clusters (
                            cluster_id, cluster_name, similarity_threshold, min_cluster_size,
                            clustering_algorithm, clustering_parameters,
                            created_from_template_hash, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (cluster_id) DO UPDATE SET
                            cluster_name = EXCLUDED.cluster_name,
                            last_updated = NOW(),
                            similarity_threshold = EXCLUDED.similarity_threshold,
                            clustering_parameters = EXCLUDED.clustering_parameters
                    """, (
                        cluster_id, cluster_name, similarity_threshold, min_cluster_size,
                        clustering_algorithm, Json(clustering_parameters or {}),
                        created_from_template
                    ))

                conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to store cluster {cluster_id}: {e}")
            return False

    def store_cluster_assignment(self,
                               template_hash: str,
                               cluster_id: int,
                               similarity_score: float,
                               confidence_score: float,
                               assignment_reason: str,
                               assignment_metadata: Dict = None) -> bool:
        """
        Store template-cluster assignment

        Args:
            template_hash: Template hash
            cluster_id: Cluster ID
            similarity_score: Similarity score
            confidence_score: Confidence in assignment
            assignment_reason: Reason for assignment
            assignment_metadata: Additional metadata

        Returns:
            True if successful, False otherwise
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    # First, mark any existing assignments as not current
                    cursor.execute("""
                        UPDATE neurdb_metrics.template_cluster_assignments
                        SET unassigned_at = NOW(), is_current = FALSE
                        WHERE template_hash = %s AND is_current = TRUE
                    """, (template_hash,))

                    # Insert new assignment
                    cursor.execute("""
                        INSERT INTO neurdb_metrics.template_cluster_assignments (
                            template_hash, cluster_id, similarity_score, confidence_score,
                            assignment_reason, assignment_metadata, assigned_at, is_current
                        ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), TRUE)
                    """, (
                        template_hash, cluster_id, similarity_score, confidence_score,
                        assignment_reason, Json(assignment_metadata or {})
                    ))

                    # Update template evolution
                    cursor.execute("""
                        INSERT INTO neurdb_metrics.template_evolution (
                            template_hash, event_type, new_cluster_id, similarity_score,
                            reason, event_metadata, event_timestamp
                        ) VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    """, (
                        template_hash, 'CLUSTER_ASSIGNED', cluster_id,
                        similarity_score, assignment_reason,
                        Json(assignment_metadata or {})
                    ))

                conn.commit()
            return True

        except Exception as e:
            logger.error(f"Failed to store cluster assignment for {template_hash}: {e}")
            return False

    def get_cluster_assignments(self, cluster_id: Optional[int] = None) -> List[Dict]:
        """
        Get current cluster assignments

        Args:
            cluster_id: Optional cluster ID filter

        Returns:
            List of assignment dictionaries
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    if cluster_id:
                        cursor.execute("""
                            SELECT * FROM neurdb_metrics.current_cluster_assignments
                            WHERE cluster_id = %s
                            ORDER BY similarity_score DESC
                        """, (cluster_id,))
                    else:
                        cursor.execute("""
                            SELECT * FROM neurdb_metrics.current_cluster_assignments
                            ORDER BY cluster_id, similarity_score DESC
                        """)

                    return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Failed to get cluster assignments: {e}")
            return []

    def get_workload_timeseries(self,
                               template_hash: Optional[str] = None,
                               start_time: Optional[datetime] = None,
                               end_time: Optional[datetime] = None) -> Dict:
        """
        Get workload time series data

        Args:
            template_hash: Optional template hash filter
            start_time: Optional start time filter
            end_time: Optional end time filter

        Returns:
            Dictionary of template_hash -> time series data
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    query = """
                        SELECT template_hash, timestamp, execution_count, avg_duration_ms
                        FROM neurdb_metrics.workload_timeseries
                        WHERE 1=1
                    """
                    params = []

                    if template_hash:
                        query += " AND template_hash = %s"
                        params.append(template_hash)

                    if start_time:
                        query += " AND timestamp >= %s"
                        params.append(start_time)

                    if end_time:
                        query += " AND timestamp <= %s"
                        params.append(end_time)

                    query += " ORDER BY template_hash, timestamp"

                    cursor.execute(query, params)
                    rows = cursor.fetchall()

                    # Organize by template_hash
                    time_series = {}
                    for row in rows:
                        template_hash = row['template_hash']
                        if template_hash not in time_series:
                            time_series[template_hash] = []

                        time_series[template_hash].append({
                            'timestamp': row['timestamp'],
                            'execution_count': row['execution_count'],
                            'avg_duration_ms': row['avg_duration_ms']
                        })

                    return time_series

        except Exception as e:
            logger.error(f"Failed to get workload timeseries: {e}")
            return {}

    def get_cluster_summary(self, cluster_id: Optional[int] = None) -> List[Dict]:
        """
        Get cluster summary information

        Args:
            cluster_id: Optional cluster ID filter

        Returns:
            List of cluster summaries
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    if cluster_id:
                        cursor.execute("""
                            SELECT * FROM neurdb_metrics.cluster_summary
                            WHERE cluster_id = %s
                        """, (cluster_id,))
                    else:
                        cursor.execute("""
                            SELECT * FROM neurdb_metrics.cluster_summary
                            ORDER BY cluster_id
                        """)

                    return [dict(row) for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Failed to get cluster summary: {e}")
            return []

    def get_template_info(self, template_hash: str) -> Optional[Dict]:
        """
        Get detailed template information

        Args:
            template_hash: Template hash

        Returns:
            Template information dictionary or None
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT * FROM neurdb_metrics.query_templates
                        WHERE template_hash = %s
                    """, (template_hash,))

                    row = cursor.fetchone()
                    return dict(row) if row else None

        except Exception as e:
            logger.error(f"Failed to get template info for {template_hash}: {e}")
            return None

    def cleanup_old_data(self, days_to_keep: int = 90) -> int:
        """
        Clean up old data

        Args:
            days_to_keep: Number of days to keep

        Returns:
            Number of deleted records
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.callproc('neurdb_metrics.cleanup_old_data', [days_to_keep])
                    deleted_count = cursor.fetchone[0]
                conn.commit()

            logger.info(f"Cleaned up {deleted_count} old records")
            return deleted_count

        except Exception as e:
            logger.error(f"Failed to cleanup old data: {e}")
            return 0

    def close(self):
        """Close database connection pool"""
        if self.pool:
            self.pool.closeall()
            logger.info("Database connection pool closed")

    def __del__(self):
        """Destructor to ensure cleanup"""
        self.close()

if __name__ == "__main__":
    # Example usage
    store = WorkloadStore(
        host='localhost',
        database='neurdb',
        user='neurdb'
    )

    try:
        # Initialize schema
        store.initialize_schema()

        # Store a sample template
        template_hash = "abc123def456"
        template_text = "SELECT * FROM users WHERE user_id = #"
        logical_features = {
            'query_type': 'SELECT_WHERE',
            'tables': ['users'],
            'has_subquery': False,
            'complexity_score': 2
        }

        store.store_query_template(template_hash, template_text, logical_features)

        # Store some time series data
        from datetime import datetime, timedelta
        for i in range(10):
            timestamp = datetime.now() - timedelta(minutes=i*5)
            store.store_workload_timeseries(template_hash, timestamp, 5 + i % 3, 10.5 + i * 0.2)

        print("Sample data stored successfully")

        # Retrieve and display
        assignments = store.get_cluster_assignments()
        print(f"Current assignments: {len(assignments)}")

        summary = store.get_cluster_summary()
        print(f"Cluster summary: {len(summary)} clusters")

    finally:
        store.close()