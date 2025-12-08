#!/usr/bin/env python3
"""Test script to debug column extraction"""

import sys
import os
sys.path.insert(0, '/Volumes/data/DB/neurdb_dev/aiengine/workload_forecast/src')

import sqlparse
from collections import defaultdict

def test_column_extraction():
    test_queries = [
        "SELECT t.title, t.production_year FROM title t WHERE t.kind_id = 4 AND t.production_year >= 2000",
        "SELECT t.title, t.production_year FROM title t WHERE t.kind_id = 4 AND t.production_year BETWEEN 2010 AND 2020 ORDER BY t.production_year DESC",
        "SELECT n.name, COUNT(*) as movie_count FROM name n JOIN cast_info ci ON n.id = ci.person_id JOIN title t ON ci.movie_id = t.id WHERE t.kind_id = 4 AND t.production_year >= 2015 GROUP BY n.name HAVING COUNT(*) > 5"
    ]

    def extract_column_usage(query: str):
        """Extract column references from query"""
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
                                    if c and '.' in c:
                                        columns[c] += 1
                            elif isinstance(col, str) and '.' in col:
                                columns[col] += 1
                    elif isinstance(token, sqlparse.sql.IdentifierList):
                        extract_identifiers(token.get_identifiers())
                    elif isinstance(token, sqlparse.sql.Function):
                        # Check function parameters
                        for param in token.get_parameters():
                            col = extract_columns_from_token(param)
                            if col:
                                if isinstance(col, list):
                                    for c in col:
                                        if c and '.' in c:
                                            columns[c] += 1
                                elif isinstance(col, str) and '.' in col:
                                    columns[col] += 1
                    # Recursively check child tokens
                    if hasattr(token, 'tokens'):
                        extract_identifiers(token.tokens)

            # Start extraction from the parsed query
            extract_identifiers(parsed.tokens)

        except Exception as e:
            print(f"SQL parsing failed for query: {query[:100]}... - {e}")
            # Fallback to simple regex extraction
            import re
            column_pattern = r'\b(\w+)\.(\w+)\b'
            for match in re.finditer(column_pattern, query):
                table, column = match.groups()
                columns[f"{table}.{column}"] += 1

        return columns

    print("Testing column extraction with sample queries:")
    print("=" * 60)

    for i, query in enumerate(test_queries, 1):
        print(f"\nQuery {i}: {query}")
        columns = extract_column_usage(query)
        print(f"Extracted columns: {dict(columns)}")

if __name__ == "__main__":
    test_column_extraction()