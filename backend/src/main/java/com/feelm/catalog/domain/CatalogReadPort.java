package com.feelm.catalog.domain;

public interface CatalogReadPort {
    CatalogModels.CatalogSnapshot loadActiveSnapshot();
}
