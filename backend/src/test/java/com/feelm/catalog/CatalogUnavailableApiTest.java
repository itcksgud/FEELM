package com.feelm.catalog;

import com.feelm.catalog.api.CatalogController;
import com.feelm.catalog.api.GlobalExceptionHandler;
import com.feelm.catalog.api.TraceIdFilter;
import com.feelm.catalog.security.CatalogUserContextResolver;
import com.feelm.catalog.service.CatalogService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataAccessResourceFailureException;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(CatalogController.class)
@Import({GlobalExceptionHandler.class, TraceIdFilter.class, CatalogUserContextResolver.class})
class CatalogUnavailableApiTest {
    @Autowired
    MockMvc mvc;

    @MockitoBean
    CatalogService catalogService;

    @Test
    void databaseFailureIsAContract503WithTraceId() throws Exception {
        when(catalogService.search(any(), any())).thenThrow(new DataAccessResourceFailureException("database offline"));

        mvc.perform(get("/api/v1/movies"))
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.code").value("CATALOG_UNAVAILABLE"))
                .andExpect(jsonPath("$.traceId").isNotEmpty())
                .andExpect(jsonPath("$.fieldErrors").isArray());
    }
}
