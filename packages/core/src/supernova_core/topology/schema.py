"""JSON Schema shared by the topology discovery prompt and normalizer."""
from __future__ import annotations

_EVIDENCE = {
    "type": "object",
    "properties": {
        "repo": {"type": "string"},
        "file": {"type": "string"},
        "line": {"type": "integer", "minimum": 1},
        "snippet": {"type": "string"},
    },
    "required": ["repo", "file", "line", "snippet"],
    "additionalProperties": False,
}

TOPOLOGY_DISCOVERY_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "CrossRepositoryTopologyDiscovery",
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "roles": {
                        "type": "array", "uniqueItems": True,
                        "items": {"enum": ["entrypoint", "backend"]},
                    },
                    "capabilities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"enum": ["entrypoint", "backend"]},
                                "confidence": {"enum": ["high", "medium", "low"]},
                                "evidence": {"type": "array", "items": _EVIDENCE},
                            },
                            "required": ["role", "confidence", "evidence"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["repo", "roles", "capabilities"],
                "additionalProperties": False,
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "protocol": {"enum": ["grpc", "http", "graphql"]},
                    "confidence": {"enum": ["high", "medium", "low"]},
                    "service": {"type": ["string", "null"]},
                    "method": {"type": ["string", "null"]},
                    "client_evidence": {"type": "array", "items": _EVIDENCE},
                    "handler_evidence": {"type": "array", "items": _EVIDENCE},
                },
                "required": [
                    "from", "to", "protocol", "confidence",
                    "client_evidence", "handler_evidence",
                ],
                "additionalProperties": False,
            },
        },
        "uncertain": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "message": {"type": "string"},
                    "protocol_hint": {"type": ["string", "null"]},
                    "evidence": {"type": "array", "items": _EVIDENCE},
                },
                "required": ["repo", "message", "protocol_hint", "evidence"],
                "additionalProperties": False,
            },
        },
        "coverage": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string"},
                    "complete": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["repo", "complete", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["nodes", "edges", "uncertain", "coverage"],
    "additionalProperties": False,
}
