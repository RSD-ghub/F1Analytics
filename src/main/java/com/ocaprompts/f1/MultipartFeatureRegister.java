package com.ocaprompts.f1;

import jakarta.ws.rs.core.Feature;
import jakarta.ws.rs.core.FeatureContext;
import jakarta.ws.rs.ext.Provider;
import org.glassfish.jersey.media.multipart.MultiPartFeature;

/**
 * Jersey Feature that registers MultiPartFeature.
 * Helidon discovers it automatically because of @Provider.
 */
@Provider
public class MultipartFeatureRegister implements Feature {
    @Override
    public boolean configure(FeatureContext ctx) {
        ctx.register(MultiPartFeature.class);
        return true;
    }
}
