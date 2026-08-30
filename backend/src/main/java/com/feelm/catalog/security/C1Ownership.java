package com.feelm.catalog.security;

import com.feelm.catalog.api.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;

import java.util.UUID;

@Component
public final class C1Ownership {
    public void requireOwner(UUID actorUserId, UUID resourceOwnerId) {
        if (actorUserId == null || !actorUserId.equals(resourceOwnerId)) {
            throw new ApiException(HttpStatus.NOT_FOUND, "RESOURCE_NOT_FOUND", "요청한 정보를 찾을 수 없어요.");
        }
    }
}
