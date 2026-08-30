FROM eclipse-temurin:17-jdk-alpine@sha256:0bd5d65efad5c8d9f8d8e6573aa5c8851237550605ff18ff78fee5810c2ebe25 AS build
WORKDIR /workspace
COPY gradlew gradlew.bat settings.gradle.kts build.gradle.kts ./
COPY gradle ./gradle
RUN chmod +x gradlew
COPY src ./src
RUN ./gradlew --no-daemon --dependency-verification strict bootJar

FROM eclipse-temurin:17-jre-alpine@sha256:27cc0849148c0fd32ee8e95988917becf9bc96a3182a24f99d9763aa8e90f8cb
WORKDIR /app
RUN addgroup -S feelm && adduser -S feelm -G feelm
COPY --from=build /workspace/build/libs/*.jar /app/feelm-catalog.jar
USER feelm
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/feelm-catalog.jar"]
