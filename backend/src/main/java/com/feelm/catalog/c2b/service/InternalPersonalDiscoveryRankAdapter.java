package com.feelm.catalog.c2b.service;

import com.feelm.catalog.c2.recommendation.InternalRecommendationService;
import com.feelm.catalog.c2.recommendation.RecommenderPort;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.util.UUID;

@Component
@Profile({"postgres", "local"})
public final class InternalPersonalDiscoveryRankAdapter implements PersonalDiscoveryRankPort {
    private final InternalRecommendationService recommendations;

    public InternalPersonalDiscoveryRankAdapter(InternalRecommendationService recommendations) {
        this.recommendations = recommendations;
    }

    @Override
    public RecommenderPort.Result rank(UUID actorUserId, UUID requestId) {
        return recommendations.rank(actorUserId, requestId);
    }
}
