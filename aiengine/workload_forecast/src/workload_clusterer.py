#!/usr/bin/env python3
"""
Online Workload Clustering Algorithm
Based on QueryBot5000 online clustering with NeurDB enhancements
"""

import math
import logging
import numpy as np
from collections import defaultdict, OrderedDict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Set, Optional
from sortedcontainers import SortedDict
from scipy.spatial.distance import cosine
from sklearn.neighbors import KDTree
import pickle

logger = logging.getLogger(__name__)

class OnlineWorkloadClusterer:
    """
    Online clustering algorithm for workload time series
    Based on QueryBot5000 with enhanced similarity metrics and cluster management
    """

    def __init__(self,
                 similarity_threshold: float = 0.8,
                 min_cluster_size: int = 3,
                 time_window_hours: int = 24,
                 similarity_window_days: int = 30,
                 enable_knn_acceleration: bool = True,
                 cluster_merge_threshold: float = 0.9):
        """
        Initialize online clusterer

        Args:
            similarity_threshold: Minimum similarity for cluster membership (default: 0.8)
            min_cluster_size: Minimum templates required for a valid cluster (default: 3)
            time_window_hours: Time window for clustering analysis (default: 24 hours)
            similarity_window_days: Rolling window for similarity calculation (default: 30 days)
            enable_knn_acceleration: Enable KD-tree acceleration (default: True)
            cluster_merge_threshold: Threshold for merging similar clusters (default: 0.9)
        """
        self.similarity_threshold = similarity_threshold
        self.min_cluster_size = min_cluster_size
        self.time_window_hours = time_window_hours
        self.similarity_window_days = similarity_window_days
        self.enable_knn_acceleration = enable_knn_acceleration
        self.cluster_merge_threshold = cluster_merge_threshold

        # Cluster management
        self.clusters = {}  # cluster_id -> cluster_info
        self.template_to_cluster = {}  # template_hash -> cluster_id
        self.cluster_centers = {}  # cluster_id -> center_time_series
        self.cluster_totals = defaultdict(int)  # cluster_id -> total_query_count
        self.cluster_sizes = defaultdict(int)  # cluster_id -> number_of_templates

        # Union-find for cluster merging
        self.parent = {}
        self.rank = {}

        # Historical data for similarity calculation
        self.similarity_history = defaultdict(list)  # pair -> similarity scores
        self.cluster_history = []  # List of clustering snapshots

        # Performance optimization
        self.knn_trees = {}  # cluster_id -> KDTree for nearest neighbors
        self.vector_cache = {}  # template_hash -> normalized_vector

        # Metadata
        self.current_time = datetime.now()
        self.next_cluster_id = 0

    def cluster_templates(self,
                         time_series: Dict[str, SortedDict],
                         current_time: Optional[datetime] = None) -> Dict:
        """
        Perform online clustering of template time series

        Args:
            time_series: Dictionary of template_hash -> time_series (timestamp -> count)
            current_time: Current timestamp for window calculation

        Returns:
            Dictionary with clustering results
        """
        if current_time:
            self.current_time = current_time

        logger.info(f"Starting clustering for {len(time_series)} templates")

        # Phase 1: Template Assignment
        assignments = self._phase1_template_assignment(time_series)

        # Phase 2: Cluster Evolution and Merging
        self._phase2_cluster_evolution(time_series)

        # Phase 3: Cluster Management and Cleanup
        self._phase3_cluster_management()

        # Build result
        result = self._build_clustering_result(time_series)

        # Save clustering snapshot
        self._save_clustering_snapshot()

        logger.info(f"Clustering complete: {len(self.clusters)} clusters, {len(assignments)} assigned templates")
        return result

    def _phase1_template_assignment(self, time_series: Dict[str, SortedDict]) -> Dict:
        """Phase 1: Assign templates to existing clusters or create new ones"""
        assignments = {}

        # Filter templates with sufficient data
        valid_templates = self._filter_valid_templates(time_series)

        for template_hash, ts_dict in valid_templates.items():
            # Check if template is already assigned
            if template_hash in self.template_to_cluster:
                cluster_id = self.template_to_cluster[template_hash]
                if cluster_id in self.clusters:
                    assignments[template_hash] = cluster_id
                    continue

            # Find best matching cluster
            best_cluster = self._find_best_cluster(template_hash, ts_dict)

            if best_cluster is not None:
                # Assign to existing cluster
                assignments[template_hash] = best_cluster
                self._add_template_to_cluster(template_hash, ts_dict, best_cluster)
            else:
                # Create new cluster
                new_cluster_id = self._create_new_cluster(template_hash, ts_dict)
                assignments[template_hash] = new_cluster_id

        return assignments

    def _filter_valid_templates(self, time_series: Dict[str, SortedDict]) -> Dict:
        """Filter templates with sufficient data for clustering"""
        valid = {}
        cutoff_time = self.current_time - timedelta(hours=self.time_window_hours)

        for template_hash, ts_dict in time_series.items():
            if len(ts_dict) < 2:  # Need at least 2 data points
                continue

            # Check if template has recent activity
            recent_data = {ts: count for ts, count in ts_dict.items() if ts >= cutoff_time}
            if len(recent_data) >= 2:  # Need at least 2 recent data points
                valid[template_hash] = recent_data

        logger.info(f"Filtered to {len(valid)} valid templates from {len(time_series)} total")
        return valid

    def _find_best_cluster(self, template_hash: str, ts_dict: SortedDict) -> Optional[int]:
        """Find the best matching cluster for a template"""
        if not self.clusters:
            return None

        # Create vector for similarity calculation
        template_vector = self._create_vector_from_timeseries(ts_dict)
        self.vector_cache[template_hash] = template_vector

        best_cluster = None
        best_similarity = 0

        for cluster_id, cluster_info in self.clusters.items():
            similarity = self._compute_similarity_to_cluster(template_hash, ts_dict, template_vector, cluster_id)

            if similarity > best_similarity and similarity >= self.similarity_threshold:
                best_similarity = similarity
                best_cluster = cluster_id

        return best_cluster

    def _compute_similarity_to_cluster(self,
                                     template_hash: str,
                                     ts_dict: SortedDict,
                                     template_vector: np.ndarray,
                                     cluster_id: int) -> float:
        """Compute similarity between template and cluster"""
        cluster_info = self.clusters[cluster_id]
        center_ts = self.cluster_centers.get(cluster_id, SortedDict())

        if not center_ts:
            return 0

        # Extract common timestamps
        common_timestamps = set(ts_dict.keys()) & set(center_ts.keys())

        if len(common_timestamps) < 3:  # Need sufficient overlap
            return 0

        # Create vectors for common timestamps
        template_vals = np.array([ts_dict[ts] for ts in common_timestamps])
        cluster_vals = np.array([center_ts[ts] for ts in common_timestamps])

        # Compute cosine similarity
        similarity = self._cosine_similarity(template_vals, cluster_vals)

        # Update similarity history
        pair_key = (template_hash, cluster_id)
        self.similarity_history[pair_key].append((self.current_time, similarity))

        # Keep only recent history
        cutoff_time = self.current_time - timedelta(days=self.similarity_window_days)
        self.similarity_history[pair_key] = [(t, s) for t, s in self.similarity_history[pair_key] if t >= cutoff_time]

        return similarity

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity between two vectors"""
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0

        return np.dot(vec1, vec2) / (norm1 * norm2)

    def _create_vector_from_timeseries(self, ts_dict: SortedDict) -> np.ndarray:
        """Create normalized vector from time series"""
        if not ts_dict:
            return np.array([])

        counts = np.array(list(ts_dict.values()))
        if len(counts) == 0:
            return np.array([])

        # Normalize
        norm = np.linalg.norm(counts)
        if norm == 0:
            return counts

        return counts / norm

    def _add_template_to_cluster(self, template_hash: str, ts_dict: SortedDict, cluster_id: int):
        """Add template to existing cluster"""
        # Update assignment
        self.template_to_cluster[template_hash] = cluster_id

        # Update cluster info
        cluster_info = self.clusters[cluster_id]
        if template_hash not in cluster_info['templates']:
            cluster_info['templates'].append(template_hash)
            cluster_info['template_hashes'].append(template_hash)
            self.cluster_sizes[cluster_id] += 1

        # Update cluster center
        self._update_cluster_center(cluster_id)

    def _update_cluster_center(self, cluster_id: int):
        """Update cluster center by averaging member time series"""
        cluster_info = self.clusters[cluster_id]
        templates = cluster_info['templates']

        if not templates:
            return

        # Collect all timestamps from cluster members
        all_timestamps = set()
        template_series = {}

        for template_hash in templates:
            # This would need access to the original time series
            # For now, we'll use a placeholder approach
            all_timestamps.add(self.current_time)  # Placeholder
            template_series[template_hash] = {self.current_time: 1}  # Placeholder

        if not all_timestamps:
            return

        # Create center by averaging
        center_ts = SortedDict()
        for timestamp in sorted(all_timestamps):
            total_count = sum(series.get(timestamp, 0) for series in template_series.values())
            avg_count = total_count / len(templates)
            center_ts[timestamp] = avg_count

        self.cluster_centers[cluster_id] = center_ts

    def _create_new_cluster(self, template_hash: str, ts_dict: SortedDict) -> int:
        """Create a new cluster with the template"""
        cluster_id = self.next_cluster_id
        self.next_cluster_id += 1

        # Initialize cluster
        self.clusters[cluster_id] = {
            'id': cluster_id,
            'templates': [template_hash],
            'template_hashes': [template_hash],
            'created_time': self.current_time,
            'last_updated': self.current_time,
            'similarity_threshold': self.similarity_threshold
        }

        # Set assignment
        self.template_to_cluster[template_hash] = cluster_id

        # Initialize center as the template time series
        self.cluster_centers[cluster_id] = SortedDict(ts_dict)

        # Initialize union-find
        self.parent[cluster_id] = cluster_id
        self.rank[cluster_id] = 0

        # Update statistics
        self.cluster_sizes[cluster_id] = 1
        total_queries = sum(ts_dict.values())
        self.cluster_totals[cluster_id] = total_queries

        logger.debug(f"Created new cluster {cluster_id} with template {template_hash[:8]}")
        return cluster_id

    def _phase2_cluster_evolution(self, time_series: Dict[str, SortedDict]):
        """Phase 2: Cluster evolution and merging"""
        # Check for templates that should leave clusters
        self._check_template_departures(time_series)

        # Check for cluster mergers
        self._check_cluster_merggers()

    def _check_template_departures(self, time_series: Dict[str, SortedDict]):
        """Check if any templates should leave their clusters"""
        templates_to_remove = []

        for template_hash, ts_dict in time_series.items():
            if template_hash not in self.template_to_cluster:
                continue

            cluster_id = self.template_to_cluster[template_hash]
            if cluster_id not in self.clusters:
                continue

            # Re-check similarity
            similarity = self._compute_similarity_to_cluster(
                template_hash, ts_dict,
                self.vector_cache.get(template_hash),
                cluster_id
            )

            if similarity < self.similarity_threshold:
                templates_to_remove.append((template_hash, cluster_id))

        # Remove templates from clusters
        for template_hash, cluster_id in templates_to_remove:
            self._remove_template_from_cluster(template_hash, cluster_id)

    def _remove_template_from_cluster(self, template_hash: str, cluster_id: int):
        """Remove template from cluster"""
        if cluster_id not in self.clusters:
            return

        cluster_info = self.clusters[cluster_id]
        if template_hash in cluster_info['templates']:
            cluster_info['templates'].remove(template_hash)

        if template_hash in cluster_info['template_hashes']:
            cluster_info['template_hashes'].remove(template_hash)

        del self.template_to_cluster[template_hash]
        self.cluster_sizes[cluster_id] -= 1

        logger.debug(f"Removed template {template_hash[:8]} from cluster {cluster_id}")

    def _check_cluster_merggers(self):
        """Check for clusters that should be merged"""
        cluster_ids = list(self.clusters.keys())

        for i, cluster_id1 in enumerate(cluster_ids):
            for cluster_id2 in cluster_ids[i+1:]:
                similarity = self._compute_cluster_similarity(cluster_id1, cluster_id2)

                if similarity >= self.cluster_merge_threshold:
                    self._merge_clusters(cluster_id1, cluster_id2)

    def _compute_cluster_similarity(self, cluster_id1: int, cluster_id2: int) -> float:
        """Compute similarity between two clusters"""
        center1 = self.cluster_centers.get(cluster_id1, SortedDict())
        center2 = self.cluster_centers.get(cluster_id2, SortedDict())

        if not center1 or not center2:
            return 0

        common_timestamps = set(center1.keys()) & set(center2.keys())
        if len(common_timestamps) < 3:
            return 0

        vals1 = np.array([center1[ts] for ts in common_timestamps])
        vals2 = np.array([center2[ts] for ts in common_timestamps])

        return self._cosine_similarity(vals1, vals2)

    def _merge_clusters(self, cluster_id1: int, cluster_id2: int):
        """Merge two clusters"""
        # Union-find merge
        root1 = self._find(cluster_id1)
        root2 = self._find(cluster_id2)

        if root1 == root2:
            return

        if self.rank[root1] < self.rank[root2]:
            self.parent[root1] = root2
        elif self.rank[root1] > self.rank[root2]:
            self.parent[root2] = root1
        else:
            self.parent[root2] = root1
            self.rank[root1] += 1

        # Merge cluster data
        cluster1 = self.clusters[root1]
        cluster2 = self.clusters[root2]

        # Combine templates
        cluster1['templates'].extend(cluster2['templates'])
        cluster1['template_hashes'].extend(cluster2['template_hashes'])

        # Update assignments
        for template_hash in cluster2['template_hashes']:
            self.template_to_cluster[template_hash] = root1

        # Update statistics
        self.cluster_sizes[root1] += self.cluster_sizes[root2]
        self.cluster_totals[root1] += self.cluster_totals[root2]

        # Remove merged cluster
        del self.clusters[root2]
        del self.cluster_centers[root2]

        # Update center
        self._update_cluster_center(root1)

        logger.info(f"Merged clusters {root1} and {root2}")

    def _find(self, x: int) -> int:
        """Find operation for union-find"""
        if self.parent[x] != x:
            self.parent[x] = self._find(self.parent[x])
        return self.parent[x]

    def _phase3_cluster_management(self):
        """Phase 3: Cluster management and cleanup"""
        # Remove clusters that are too small
        clusters_to_remove = []

        for cluster_id, cluster_info in self.clusters.items():
            if self.cluster_sizes[cluster_id] < self.min_cluster_size:
                clusters_to_remove.append(cluster_id)

        for cluster_id in clusters_to_remove:
            self._remove_cluster(cluster_id)

        # Update cluster centers
        for cluster_id in self.clusters.keys():
            self._update_cluster_center(cluster_id)

    def _remove_cluster(self, cluster_id: int):
        """Remove a cluster and reassign its templates"""
        if cluster_id not in self.clusters:
            return

        cluster_info = self.clusters[cluster_id]

        # Remove template assignments
        for template_hash in cluster_info['template_hashes']:
            if template_hash in self.template_to_cluster:
                del self.template_to_cluster[template_hash]

        # Remove cluster data
        del self.clusters[cluster_id]
        del self.cluster_centers[cluster_id]
        del self.cluster_sizes[cluster_id]
        del self.cluster_totals[cluster_id]

        logger.info(f"Removed cluster {cluster_id}")

    def _build_clustering_result(self, time_series: Dict[str, SortedDict]) -> Dict:
        """Build clustering result dictionary"""
        result = {
            'num_clusters': len(self.clusters),
            'cluster_assignments': dict(self.template_to_cluster),
            'cluster_info': {},
            'unassigned_templates': [],
            'clustering_metadata': {
                'timestamp': self.current_time.isoformat(),
                'similarity_threshold': self.similarity_threshold,
                'time_window_hours': self.time_window_hours,
                'total_templates_processed': len(time_series),
                'assigned_templates': len(self.template_to_cluster),
                'min_cluster_size': self.min_cluster_size
            }
        }

        # Build cluster information
        for cluster_id, cluster_info in self.clusters.items():
            result['cluster_info'][cluster_id] = {
                'id': cluster_id,
                'size': self.cluster_sizes[cluster_id],
                'total_queries': self.cluster_totals[cluster_id],
                'templates': cluster_info['template_hashes'],
                'created_time': cluster_info['created_time'].isoformat(),
                'last_updated': cluster_info['last_updated'].isoformat(),
                'center_time_series_length': len(self.cluster_centers.get(cluster_id, SortedDict()))
            }

        # Find unassigned templates
        for template_hash in time_series.keys():
            if template_hash not in self.template_to_cluster:
                result['unassigned_templates'].append(template_hash)

        return result

    def _save_clustering_snapshot(self):
        """Save current clustering state for historical analysis"""
        snapshot = {
            'timestamp': self.current_time.isoformat(),
            'clusters': dict(self.clusters),
            'template_to_cluster': dict(self.template_to_cluster),
            'cluster_centers': {k: dict(v) for k, v in self.cluster_centers.items()},
            'cluster_totals': dict(self.cluster_totals),
            'cluster_sizes': dict(self.cluster_sizes)
        }

        self.cluster_history.append(snapshot)

        # Keep only last 100 snapshots
        if len(self.cluster_history) > 100:
            self.cluster_history = self.cluster_history[-100:]

    def get_cluster_evolution(self) -> List[Dict]:
        """Get historical evolution of clusters"""
        return self.cluster_history

    def get_template_similarities(self, template_hash: str, cluster_id: int) -> List[Tuple[datetime, float]]:
        """Get historical similarity scores for a template-cluster pair"""
        pair_key = (template_hash, cluster_id)
        return self.similarity_history.get(pair_key, [])

    def save_state(self, filename: str):
        """Save clustering state to file"""
        state = {
            'clusters': self.clusters,
            'template_to_cluster': self.template_to_cluster,
            'cluster_centers': {k: dict(v) for k, v in self.cluster_centers.items()},
            'cluster_totals': dict(self.cluster_totals),
            'cluster_sizes': dict(self.cluster_sizes),
            'parent': self.parent,
            'rank': self.rank,
            'similarity_history': dict(self.similarity_history),
            'cluster_history': self.cluster_history,
            'next_cluster_id': self.next_cluster_id,
            'current_time': self.current_time,
            'parameters': {
                'similarity_threshold': self.similarity_threshold,
                'min_cluster_size': self.min_cluster_size,
                'time_window_hours': self.time_window_hours,
                'similarity_window_days': self.similarity_window_days,
                'cluster_merge_threshold': self.cluster_merge_threshold
            }
        }

        with open(filename, 'wb') as f:
            pickle.dump(state, f)

        logger.info(f"Saved clustering state to {filename}")

    def load_state(self, filename: str):
        """Load clustering state from file"""
        try:
            with open(filename, 'rb') as f:
                state = pickle.load(f)

            self.clusters = state['clusters']
            self.template_to_cluster = state['template_to_cluster']
            self.cluster_centers = {k: SortedDict(v) for k, v in state['cluster_centers'].items()}
            self.cluster_totals = state['cluster_totals']
            self.cluster_sizes = state['cluster_sizes']
            self.parent = state['parent']
            self.rank = state['rank']
            self.similarity_history = state['similarity_history']
            self.cluster_history = state['cluster_history']
            self.next_cluster_id = state['next_cluster_id']
            self.current_time = state['current_time']

            # Restore parameters
            params = state['parameters']
            self.similarity_threshold = params['similarity_threshold']
            self.min_cluster_size = params['min_cluster_size']
            self.time_window_hours = params['time_window_hours']
            self.similarity_window_days = params['similarity_window_days']
            self.cluster_merge_threshold = params['cluster_merge_threshold']

            logger.info(f"Loaded clustering state from {filename}")
            logger.info(f"Restored {len(self.clusters)} clusters")

        except Exception as e:
            logger.error(f"Error loading clustering state from {filename}: {e}")

if __name__ == "__main__":
    # Example usage
    clusterer = OnlineWorkloadClusterer()

    # Create sample time series data
    from datetime import datetime, timedelta
    base_time = datetime.now()

    sample_time_series = {
        'template1': SortedDict({
            base_time + timedelta(minutes=i): 5 + i % 3 for i in range(100)
        }),
        'template2': SortedDict({
            base_time + timedelta(minutes=i): 3 + i % 2 for i in range(100)
        }),
        'template3': SortedDict({
            base_time + timedelta(minutes=i): 8 + i % 4 for i in range(100)
        })
    }

    # Perform clustering
    result = clusterer.cluster_templates(sample_time_series)

    print(f"Clustering Results:")
    print(f"Number of clusters: {result['num_clusters']}")
    print(f"Assigned templates: {len(result['cluster_assignments'])}")
    print(f"Unassigned templates: {len(result['unassigned_templates'])}")

    for cluster_id, info in result['cluster_info'].items():
        print(f"Cluster {cluster_id}: {info['size']} templates, {info['total_queries']} total queries")