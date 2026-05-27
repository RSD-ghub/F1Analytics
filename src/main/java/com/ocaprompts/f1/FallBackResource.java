package com.ocaprompts.f1;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;


import java.io.InputStream;

/**
 * SPA fallback â€“ *must be processed after* all real API endpoints.
 * Matches every remaining GET and returns index.html
 */
@ApplicationScoped
@Path("/")                       // root resource
public class FallBackResource {

    @GET
    @Path("{path: .*}")          // catch-all  (regex ".*")
    @Produces(MediaType.TEXT_HTML)
    public Response getIndex() {

        // 1. load index.html from the same place the static handler uses
        //    (web/f1/index.html in your resources directory)
        InputStream html = getClass()
                .getClassLoader()
                .getResourceAsStream("web/f1/index.html");

        if (html == null) {                       // should never happen
            return Response.status(Response.Status.NOT_FOUND).build();
        }
        return Response.ok(html).build();         // 200 text/html
    }
}
