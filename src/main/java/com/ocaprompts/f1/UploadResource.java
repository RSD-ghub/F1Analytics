package com.ocaprompts.f1;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.ws.rs.*;                       // Path annotation is here
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;

import org.glassfish.jersey.media.multipart.FormDataBodyPart;
import org.glassfish.jersey.media.multipart.FormDataMultiPart;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;

@ApplicationScoped
@Path("/f1/upload")
public class UploadResource {

    /* where files are stored on disk */
    /*private static final java.nio.file.Path ROOT =
            java.nio.file.Paths.get("C:/Deepu/OCA-Prompts/uploads");*/

    private static final java.nio.file.Path ROOT = java.nio.file.Paths.get("/scratch/oipa/OIPA/SIT1/uploads");

    /* ---------------- POST /f1/upload/{folder} ---------------- */
    @POST
    @Path("{folder}")
    @Consumes(MediaType.MULTIPART_FORM_DATA)
    public Response upload(@PathParam("folder") String folder,
                           FormDataMultiPart mp) throws IOException {

        if (!folder.matches("[A-Za-z0-9\\-]+_[0-9]+")) {
            return Response.status(Response.Status.BAD_REQUEST)
                    .entity("Bad folder name").build();
        }

        java.nio.file.Path dir = ROOT.resolve(folder).normalize();
        Files.createDirectories(dir);

        for (FormDataBodyPart part : mp.getFields("files")) {
            String safe = Paths.get(part.getContentDisposition()
                            .getFileName())
                    .getFileName().toString();

            try (InputStream in = part.getEntityAs(InputStream.class)) {
                Files.copy(in, dir.resolve(safe),
                        StandardCopyOption.REPLACE_EXISTING);
            }
        }
        return Response.ok().build();
    }

    /* ------------- GET /f1/upload/{folder}/{name} ------------- */
    @GET
    @Path("{folder}/{name}")
    @Produces(MediaType.APPLICATION_OCTET_STREAM)
    public Response download(@PathParam("folder") String folder,
                             @PathParam("name")   String name) throws IOException {

        /* fully-qualify the type to avoid the name clash */
        java.nio.file.Path p = ROOT.resolve(folder).resolve(name).normalize();

        if (!p.startsWith(ROOT) || !Files.exists(p)) {
            return Response.status(Response.Status.NOT_FOUND).build();
        }

        String mime = Files.probeContentType(p);
        return Response.ok(p.toFile(),
                        mime != null ? mime : MediaType.APPLICATION_OCTET_STREAM)
                .header("Content-Disposition",
                        "inline; filename=\"" + p.getFileName() + '"')
                .build();
    }
}

