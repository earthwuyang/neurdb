#!/usr/bin/env python3
"""
Advanced SQL Templatizer for Workload Forecasting
Based on QueryBot5000 templatizer with enhanced normalization
"""

import re
import hashlib
import logging
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Set
from sortedcontainers import SortedDict

logger = logging.getLogger(__name__)

class AdvancedTemplatizer:
    """
    Advanced SQL templatizer with comprehensive normalization
    Based on QueryBot5000 approach with NeurDB enhancements
    """

    def __init__(self,
                 granularity_minutes: int = 1,
                 similarity_threshold: float = 0.8,
                 enable_logical_features: bool = True):
        """
        Initialize templatizer

        Args:
            granularity_minutes: Time granularity for time series (default: 1 minute)
            similarity_threshold: Threshold for template similarity (default: 0.8)
            enable_logical_features: Enable logical feature extraction (default: True)
        """
        self.granularity_minutes = granularity_minutes
        self.similarity_threshold = similarity_threshold
        self.enable_logical_features = enable_logical_features

        # Enhanced normalization patterns (QueryBot5000 inspired)
        self.patterns = [
            # Hash values and UUIDs
            (r'\'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\'', '@@@'),  # UUID
            (r'\'[0-9a-fA-F]{32,}\'', '@@@'),  # Long hex strings

            # Numeric literals (with sign and decimal support)
            (r'(?<!\w)(-?\d+\.\d+)(?!\w)', '#'),  # Decimal numbers
            (r'(?<!\w)(-?\d+)(?!\w)', '#'),      # Integers

            # String literals (enhanced handling)
            (r'\'((?:[^\']|\'\')*)\'', '\'&&&\''),  # Single-quoted strings (handles escaped quotes)
            (r'"((?:[^"]|"")*)"', '"&&&"'),        # Double-quoted strings (handles escaped quotes)

            # Boolean and null values
            (r'\bTRUE\b', '#'),   # TRUE -> #
            (r'\bFALSE\b', '#'),  # FALSE -> #
            (r'\bNULL\b', '#'),   # NULL -> #

            # Date and time literals
            (r'\'\d{4}-\d{2}-\d{2}\'', '@@@'),      # Date: '2023-12-04'
            (r'\'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\'', '@@@'),  # DateTime
            (r'\'\d{2}:\d{2}:\d{2}\'', '@@@'),       # Time

            # IN clause with lists
            (r'IN\s*\([^)]+\)', 'IN (###)'),  # IN (1,2,3) -> IN (###)

            # LIMIT clauses
            (r'\bLIMIT\s+\d+', 'LIMIT #'),    # LIMIT 100 -> LIMIT #

            # OFFSET clauses
            (r'\bOFFSET\s+\d+', 'OFFSET #'),  # OFFSET 50 -> OFFSET #
        ]

        # Compile patterns for performance
        self.compiled_patterns = [(re.compile(pattern, re.IGNORECASE), replacement)
                                for pattern, replacement in self.patterns]

        # Schema information for logical features
        self.tables_info = {}
        self.columns_info = {}

        # Cache for template hashes
        self.template_cache = {}

    def set_schema_info(self, tables_info: Dict, columns_info: Dict):
        """Set database schema information for logical feature extraction"""
        self.tables_info = tables_info
        self.columns_info = columns_info
        logger.info(f"Loaded schema: {len(tables_info)} tables, {sum(len(cols) for cols in columns_info.values())} columns")

    def normalize_query(self, query: str) -> str:
        """
        Normalize SQL query by replacing literals with placeholders

        Args:
            query: Original SQL query

        Returns:
            Normalized query template
        """
        if not query:
            return ""

        # Remove extra whitespace and normalize
        query = re.sub(r'\s+', ' ', query.strip())

        # Apply normalization patterns
        normalized = query
        for pattern, replacement in self.compiled_patterns:
            normalized = pattern.sub(replacement, normalized)

        # Normalize keywords to uppercase for consistency
        keywords = ['SELECT', 'FROM', 'WHERE', 'JOIN', 'INNER', 'LEFT', 'RIGHT', 'OUTER',
                   'GROUP', 'BY', 'ORDER', 'HAVING', 'UNION', 'AND', 'OR', 'NOT', 'IN',
                   'EXISTS', 'BETWEEN', 'LIKE', 'IS', 'NULL', 'TRUE', 'FALSE', 'LIMIT', 'OFFSET']

        for keyword in keywords:
            normalized = re.sub(r'\b' + keyword + r'\b', keyword.upper(), normalized, flags=re.IGNORECASE)

        return normalized.strip()

    def extract_template_hash(self, query: str) -> Tuple[str, str]:
        """
        Extract template and compute hash

        Args:
            query: Original SQL query

        Returns:
            Tuple of (template_hash, normalized_template)
        """
        if not query:
            return "", ""

        # Check cache first
        if query in self.template_cache:
            return self.template_cache[query]

        template = self.normalize_query(query)
        template_hash = hashlib.md5(template.encode()).hexdigest()

        # Cache result
        self.template_cache[query] = (template_hash, template)

        return template_hash, template

    def extract_logical_features(self, query: str, template: str) -> Dict:
        """
        Extract logical features from query template

        Args:
            query: Original SQL query
            template: Normalized query template

        Returns:
            Dictionary of logical features
        """
        if not self.enable_logical_features:
            return {}

        features = {
            'query_type': self._extract_query_type(template),
            'tables': set(),
            'columns': set(),
            'join_types': set(),
            'has_aggregation': False,
            'has_subquery': False,
            'has_cte': False,
            'complexity_score': 0
        }

        # Extract table and column references (simplified)
        # In production, this would use a proper SQL parser like pglast

        # Extract FROM clause tables
        from_match = re.search(r'FROM\s+([^\s\(]+)', template, re.IGNORECASE)
        if from_match:
            table_name = from_match.group(1)
            if table_name.upper() not in ['SELECT', 'DUAL']:
                features['tables'].add(table_name)

        # Extract JOIN tables
        join_matches = re.findall(r'(?:INNER|LEFT|RIGHT|FULL|CROSS)\s+JOIN\s+([^\s\(]+)', template, re.IGNORECASE)
        for table_name in join_matches:
            features['tables'].add(table_name)
            features['join_types'].add('JOIN')

        # Extract common patterns
        features['has_aggregation'] = bool(re.search(r'\b(COUNT|SUM|AVG|MIN|MAX|STDDEV|VARIANCE)\s*\(', template, re.IGNORECASE))
        features['has_subquery'] = bool(re.search(r'\([^\']*SELECT', template, re.IGNORECASE))
        features['has_cte'] = bool(re.search(r'\bWITH\s+\w+\s+AS\s*\(', template, re.IGNORECASE))

        # Simple complexity scoring
        features['complexity_score'] = (
            len(features['tables']) * 2 +
            len(features['join_types']) * 3 +
            (1 if features['has_aggregation'] else 0) * 2 +
            (1 if features['has_subquery'] else 0) * 3 +
            (1 if features['has_cte'] else 0) * 2
        )

        # Convert sets to sorted lists for serialization
        features['tables'] = sorted(list(features['tables']))
        features['columns'] = sorted(list(features['columns']))
        features['join_types'] = sorted(list(features['join_types']))

        return features

    def _extract_query_type(self, template: str) -> str:
        """Extract query type from template"""
        template_upper = template.upper()

        if template_upper.startswith('SELECT'):
            # Check for specific SELECT patterns
            if 'GROUP BY' in template_upper:
                return 'SELECT_GROUP'
            elif 'ORDER BY' in template_upper:
                return 'SELECT_ORDER'
            elif 'JOIN' in template_upper:
                return 'SELECT_JOIN'
            elif 'WHERE' in template_upper:
                return 'SELECT_WHERE'
            else:
                return 'SELECT_SIMPLE'
        elif template_upper.startswith('INSERT'):
            return 'INSERT'
        elif template_upper.startswith('UPDATE'):
            return 'UPDATE'
        elif template_upper.startswith('DELETE'):
            return 'DELETE'
        elif 'WITH' in template_upper and 'SELECT' in template_upper:
            return 'CTE_SELECT'
        else:
            return 'OTHER'

    def create_time_series(self, query_data: List[Dict]) -> Dict[str, SortedDict]:
        """
        Create time series from query data

        Args:
            query_data: List of dictionaries with 'timestamp', 'template_hash', etc.

        Returns:
            Dictionary mapping template_hash -> SortedDict(timestamp -> count)
        """
        time_series = defaultdict(SortedDict)

        for entry in query_data:
            timestamp_str = entry.get('timestamp')
            template_hash = entry.get('template_hash')

            if not timestamp_str or not template_hash:
                continue

            # Parse timestamp
            try:
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except ValueError:
                try:
                    # Try alternative format
                    timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    logger.warning(f"Cannot parse timestamp: {timestamp_str}")
                    continue

            # Round to granularity boundary
            timestamp = self._round_timestamp(timestamp)

            # Increment count
            if timestamp not in time_series[template_hash]:
                time_series[template_hash][timestamp] = 0
            time_series[template_hash][timestamp] += 1

        # Convert to regular dict and ensure continuous time series
        result = {}
        for template_hash, ts_dict in time_series.items():
            result[template_hash] = self._fill_missing_timestamps(ts_dict)

        return result

    def _round_timestamp(self, timestamp: datetime) -> datetime:
        """Round timestamp to granularity boundary"""
        total_minutes = timestamp.hour * 60 + timestamp.minute
        rounded_minutes = (total_minutes // self.granularity_minutes) * self.granularity_minutes

        return timestamp.replace(
            hour=rounded_minutes // 60,
            minute=rounded_minutes % 60,
            second=0,
            microsecond=0
        )

    def _fill_missing_timestamps(self, ts_dict: SortedDict) -> SortedDict:
        """Fill missing timestamps with zero counts for continuous time series"""
        if not ts_dict:
            return SortedDict()

        # Get time range
        start_time = next(iter(ts_dict))
        end_time = next(reversed(ts_dict))

        # Create continuous time series
        continuous_series = SortedDict()
        current_time = start_time

        while current_time <= end_time:
            continuous_series[current_time] = ts_dict.get(current_time, 0)
            current_time += timedelta(minutes=self.granularity_minutes)

        return continuous_series

    def compute_template_statistics(self, time_series: Dict[str, SortedDict]) -> Dict[str, Dict]:
        """
        Compute statistics for each template

        Args:
            time_series: Dictionary of template time series

        Returns:
            Dictionary of template statistics
        """
        stats = {}

        for template_hash, ts_dict in time_series.items():
            if not ts_dict:
                continue

            counts = list(ts_dict.values())
            timestamps = list(ts_dict.keys())

            stats[template_hash] = {
                'total_queries': sum(counts),
                'avg_queries_per_interval': sum(counts) / len(counts) if counts else 0,
                'max_queries_per_interval': max(counts) if counts else 0,
                'min_queries_per_interval': min(counts) if counts else 0,
                'time_span_hours': (timestamps[-1] - timestamps[0]).total_seconds() / 3600 if len(timestamps) > 1 else 0,
                'active_intervals': len([c for c in counts if c > 0]),
                'total_intervals': len(counts),
                'activity_ratio': len([c for c in counts if c > 0]) / len(counts) if counts else 0
            }

        return stats

    def export_to_csv(self, time_series: Dict[str, SortedDict], output_dir: str):
        """
        Export time series to CSV files (QueryBot5000 format)

        Args:
            time_series: Dictionary of template time series
            output_dir: Directory to save CSV files
        """
        import os
        os.makedirs(output_dir, exist_ok=True)

        for template_hash, ts_dict in time_series.items():
            if not ts_dict:
                continue

            filename = os.path.join(output_dir, f"template_{template_hash[:8]}.csv")

            with open(filename, 'w') as f:
                # Write header with total count
                total_count = sum(ts_dict.values())
                f.write(f"{total_count}\n")

                # Write timestamp,count pairs
                for timestamp, count in ts_dict.items():
                    f.write(f"{timestamp.isoformat()},{count}\n")

            logger.info(f"Exported template {template_hash[:8]} to {filename}")

# Utility functions for database integration
def load_workload_from_csv(csv_file: str, templatizer: AdvancedTemplatizer = None) -> Tuple[List[Dict], Dict[str, SortedDict]]:
    """
    Load workload data from CSV file and create time series

    Args:
        csv_file: Path to CSV file
        templatizer: Templatizer instance (optional)

    Returns:
        Tuple of (query_data, time_series)
    """
    if templatizer is None:
        templatizer = AdvancedTemplatizer()

    query_data = []

    try:
        with open(csv_file, 'r') as f:
            lines = f.readlines()

        # Skip header if present
        if lines and 'timestamp' in lines[0]:
            lines = lines[1:]

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse CSV: timestamp,template_hash,template,original_query
            parts = line.split(',', 3)
            if len(parts) >= 4:
                query_data.append({
                    'timestamp': parts[0],
                    'template_hash': parts[1],
                    'template': parts[2].strip('"'),
                    'original_query': parts[3].strip('"')
                })

    except Exception as e:
        logger.error(f"Error loading workload from {csv_file}: {e}")
        return [], {}

    # Create time series
    time_series = templatizer.create_time_series(query_data)

    logger.info(f"Loaded {len(query_data)} queries, {len(time_series)} unique templates")
    return query_data, time_series

if __name__ == "__main__":
    # Example usage
    templatizer = AdvancedTemplatizer()

    # Test queries
    test_queries = [
        "SELECT * FROM users WHERE user_id = 12345",
        "SELECT * FROM users WHERE user_id = 67890",
        "SELECT name, email FROM customers WHERE status = 'active'",
        "SELECT name, email FROM customers WHERE status = 'inactive'",
        "SELECT COUNT(*) FROM orders WHERE created_at >= '2023-12-01'",
        "SELECT product_id, SUM(quantity) FROM order_items GROUP BY product_id"
    ]

    for query in test_queries:
        template_hash, template = templatizer.extract_template_hash(query)
        features = templatizer.extract_logical_features(query, template)

        print(f"Query: {query}")
        print(f"Template: {template}")
        print(f"Hash: {template_hash}")
        print(f"Features: {features}")
        print("-" * 80)