package com.feelm.catalog.c2b.service;

import com.feelm.catalog.c2.recommendation.RecommenderPort;

import java.util.UUID;

public interface PersonalDiscoveryRankPort {
    RecommenderPort.Result rank(UUID actorUserId, UUID requestId);
}
