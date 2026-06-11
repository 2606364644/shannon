"""Attack chain builder.

Assembles multi-step attack chains from framework analysis and frontend
mapping results.
Ported from /root/shannon/apps/worker/src/services/attack-chain-builder.ts
"""

from __future__ import annotations

import logging

from shannon_core.services.framework_analyzer import FrameworkAnalysisResult
from shannon_core.services.frontend_mapper import FrontendAnalysisResult
from shannon_core.services.route_chain_builder import (
    AttackChain,
    build_attack_chains_from_analysis,
)


async def build_attack_chains(
    framework_result: FrameworkAnalysisResult,
    frontend_result: FrontendAnalysisResult,
    logger: logging.Logger,
) -> list[AttackChain]:
    """Build attack chains from analysis results.

    1. Call build_attack_chains_from_analysis() for base chains
    2. Return complete attack chain list
    """
    framework_endpoints = framework_result.inferred_endpoints
    frontend_routes = frontend_result.routes
    xss_chains = frontend_result.xss_chains

    chains = build_attack_chains_from_analysis(
        framework_endpoints, frontend_routes, xss_chains, logger,
    )

    logger.info("Built %d attack chain(s) from shared knowledge", len(chains))
    return chains
