# Sanctions List Ingestion & Normalization Framework

Version 1.0. The implementation follows the supplied project specification and
the automated data-acquisition addendum. Official URLs are maintained only in
`config/sources.yaml`; application code contains no provider URLs.

The acquisition pipeline reads enabled sources, streams each response, follows
redirects, retries transient failures, validates non-empty XML, calculates
SHA256, skips unchanged data, preserves changed raw versions, invokes the
configured parser and exports normalized relational datasets. A failure in one
source does not stop other sources.
