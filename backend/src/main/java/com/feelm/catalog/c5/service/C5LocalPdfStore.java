package com.feelm.catalog.c5.service;

import com.feelm.catalog.c5.api.C5ApiDtos.ReportMovieItem;
import com.feelm.catalog.c5.api.C5ApiDtos.TasteReport;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Profile;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.channels.Channels;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;

@Component
@Profile("local")
@ConditionalOnProperty(name = "c5.local.enabled", havingValue = "true")
public final class C5LocalPdfStore {
    private static final int LINES_PER_PAGE = 42;
    private final Path root;
    private final SecureRandom random = new SecureRandom();

    public C5LocalPdfStore(@Value("${c5.artifact-directory:}") String configuredRoot) {
        Path selected = configuredRoot == null || configuredRoot.isBlank()
                ? Path.of(System.getProperty("java.io.tmpdir"), "feelm-c5-artifacts")
                : Path.of(configuredRoot);
        try {
            Path normalized = selected.toAbsolutePath().normalize();
            Files.createDirectories(normalized);
            this.root = normalized.toRealPath();
        } catch (IOException exception) {
            throw new IllegalStateException("Cannot initialize local C5 artifact directory", exception);
        }
    }

    public StoredArtifact render(TasteReport report, List<ReportMovieItem> allItems) {
        try {
            byte[] bytes = renderPdf(report, allItems);
            byte[] opaque = new byte[16];
            random.nextBytes(opaque);
            Path path = root.resolve("artifact-" + HexFormat.of().formatHex(opaque) + ".pdf").normalize();
            if (!path.startsWith(root)) throw new IllegalStateException("Invalid artifact path");
            Files.write(path, bytes, StandardOpenOption.CREATE_NEW, StandardOpenOption.WRITE);
            return new StoredArtifact(path.toString(), sha256(bytes), bytes.length);
        } catch (IOException exception) {
            throw new LocalArtifactException("Local PDF rendering failed", exception);
        }
    }

    public byte[] read(String opaquePath) {
        Path path = validatedPath(opaquePath);
        try (SeekableByteChannel channel = Files.newByteChannel(
                path, Set.of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS));
             InputStream input = Channels.newInputStream(channel)) {
            // The no-follow descriptor is opened before bytes are read, removing the
            // check-then-open window in which an artifact could be replaced by a symlink.
            return input.readAllBytes();
        } catch (IOException exception) {
            throw new LocalArtifactException("Local PDF artifact is unavailable", exception);
        }
    }

    public void delete(String opaquePath) {
        try {
            Path path = validatedPath(opaquePath);
            if (!Files.exists(path, LinkOption.NOFOLLOW_LINKS)) return;
            requireRegularFile(path);
            Files.delete(path);
        } catch (IOException exception) {
            throw new LocalArtifactException("Local PDF cleanup failed", exception);
        }
    }

    private Path validatedPath(String value) {
        Path path = Path.of(value).toAbsolutePath().normalize();
        String filename = path.getFileName() == null ? "" : path.getFileName().toString();
        if (!path.getParent().equals(root)
                || !filename.matches("^artifact-[a-f0-9]{32}\\.pdf$")) {
            throw new LocalArtifactException("Artifact escaped local root", null);
        }
        return path;
    }

    private void requireRegularFile(Path path) {
        try {
            BasicFileAttributes attributes = Files.readAttributes(
                    path, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
            if (!attributes.isRegularFile() || attributes.isSymbolicLink()) {
                throw new LocalArtifactException("Artifact is not a regular local file", null);
            }
        } catch (IOException exception) {
            throw new LocalArtifactException("Local PDF artifact is unavailable", exception);
        }
    }

    static byte[] renderPdf(TasteReport report, List<ReportMovieItem> allItems) throws IOException {
        List<String> lines = new ArrayList<>();
        lines.add("FEELM Factual Half-Year Report");
        lines.add("Period: " + report.periodStart() + " to " + report.periodEnd());
        lines.add("Viewing count: " + report.metrics().viewingCount());
        lines.add("Rated count: " + report.metrics().ratedCount());
        lines.add("Average rating: " + (report.metrics().averageRating() == null ? "N/A" : report.metrics().averageRating()));
        lines.add("Movies:");
        int number = 1;
        for (ReportMovieItem item : allItems) {
            lines.add(number++ + ". " + item.displayTitle() + " | watched " + item.watchedAt()
                    + " | rating " + (item.rating() == null ? "N/A" : item.rating()));
        }
        if (allItems.isEmpty()) lines.add("No activity in this period.");

        List<List<String>> pages = new ArrayList<>();
        for (int offset = 0; offset < lines.size(); offset += LINES_PER_PAGE) {
            pages.add(List.copyOf(lines.subList(offset, Math.min(lines.size(), offset + LINES_PER_PAGE))));
        }

        List<byte[]> objects = new ArrayList<>();
        int catalogId = 1;
        int pagesId = 2;
        int fontId = 3;
        objects.add(bytes("<< /Type /Catalog /Pages 2 0 R >>"));
        StringBuilder kids = new StringBuilder();
        for (int index = 0; index < pages.size(); index++) {
            int pageId = 4 + index * 2;
            kids.append(pageId).append(" 0 R ");
        }
        objects.add(bytes("<< /Type /Pages /Kids [" + kids + "] /Count " + pages.size() + " >>"));
        objects.add(bytes("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"));
        for (int index = 0; index < pages.size(); index++) {
            int contentId = 5 + index * 2;
            objects.add(bytes("<< /Type /Page /Parent " + pagesId + " 0 R /MediaBox [0 0 595 842] "
                    + "/Resources << /Font << /F1 " + fontId + " 0 R >> >> /Contents " + contentId + " 0 R >>"));
            byte[] stream = pageStream(pages.get(index));
            ByteArrayOutputStream content = new ByteArrayOutputStream();
            content.write(bytes("<< /Length " + stream.length + " >>\nstream\n"));
            content.write(stream);
            content.write(bytes("\nendstream"));
            objects.add(content.toByteArray());
        }

        ByteArrayOutputStream pdf = new ByteArrayOutputStream();
        pdf.write(bytes("%PDF-1.7\n% deterministic-local-c5\n"));
        List<Integer> offsets = new ArrayList<>();
        offsets.add(0);
        for (int index = 0; index < objects.size(); index++) {
            offsets.add(pdf.size());
            pdf.write(bytes((index + 1) + " 0 obj\n"));
            pdf.write(objects.get(index));
            pdf.write(bytes("\nendobj\n"));
        }
        int xref = pdf.size();
        pdf.write(bytes("xref\n0 " + (objects.size() + 1) + "\n0000000000 65535 f \n"));
        for (int index = 1; index < offsets.size(); index++) {
            pdf.write(bytes(String.format("%010d 00000 n \n", offsets.get(index))));
        }
        pdf.write(bytes("trailer\n<< /Size " + (objects.size() + 1) + " /Root " + catalogId + " 0 R >>\n"
                + "startxref\n" + xref + "\n%%EOF\n"));
        return pdf.toByteArray();
    }

    private static byte[] pageStream(List<String> lines) {
        StringBuilder stream = new StringBuilder("BT\n/F1 10 Tf\n48 792 Td\n");
        for (String line : lines) {
            String visible = asciiFallback(line);
            stream.append("/Span << /ActualText <").append(utf16Hex(line)).append("> >> BDC\n")
                    .append('(').append(pdfEscape(visible)).append(") Tj\nEMC\n0 -17 Td\n");
        }
        stream.append("ET");
        return bytes(stream.toString());
    }

    private static String asciiFallback(String value) {
        StringBuilder result = new StringBuilder();
        for (char character : value.toCharArray()) {
            result.append(character >= 32 && character <= 126 ? character : '?');
        }
        return result.toString();
    }

    private static String utf16Hex(String value) {
        return HexFormat.of().withUpperCase().formatHex(("\uFEFF" + value).getBytes(StandardCharsets.UTF_16BE));
    }

    private static String pdfEscape(String value) {
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)");
    }

    private static String sha256(byte[] value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value));
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static byte[] bytes(String value) {
        return value.getBytes(StandardCharsets.US_ASCII);
    }

    public record StoredArtifact(String opaquePath, String sha256, long size) {
    }

    public static final class LocalArtifactException extends RuntimeException {
        LocalArtifactException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
