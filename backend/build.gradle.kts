import groovy.json.JsonOutput

plugins {
    java
    id("org.springframework.boot") version "3.5.16"
    id("io.spring.dependency-management") version "1.1.7"
}

group = "com.feelm"
version = "0.1.0-SNAPSHOT"

// Spring Boot 3.5.16 stays as the compatibility baseline. These patch-level
// BOM overrides close advisories published after that release.
extra["jackson-bom.version"] = "2.21.5"
extra["log4j2.version"] = "2.25.5"
extra["postgresql.version"] = "42.7.12"

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(17)
    }
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-actuator")
    implementation("org.springframework.boot:spring-boot-starter-jdbc")
    implementation("org.springframework.boot:spring-boot-starter-validation")
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.flywaydb:flyway-core")
    implementation("org.flywaydb:flyway-database-postgresql")
    implementation("org.bouncycastle:bcprov-jdk18on:1.85.2")
    runtimeOnly("org.postgresql:postgresql")

    testImplementation("org.springframework.boot:spring-boot-starter-test")
    testImplementation("org.testcontainers:junit-jupiter")
    testImplementation("org.testcontainers:postgresql")
    // Testcontainers 1.21.3 declares 1.24.0, which is affected by CVE-2024-25710/26308.
    testImplementation("org.apache.commons:commons-compress:1.28.0")
}

tasks.withType<Test> {
    useJUnitPlatform()
}

tasks.register("writeRuntimeCycloneDx") {
    group = "verification"
    description = "Writes a runtime-only CycloneDX inventory for OSV Scanner."

    val reportFile = layout.buildDirectory.file("reports/runtime.cdx.json")
    outputs.file(reportFile)

    doLast {
        val components = configurations.runtimeClasspath.get()
            .resolvedConfiguration
            .resolvedArtifacts
            .map { artifact ->
                val module = artifact.moduleVersion.id
                linkedMapOf(
                    "type" to "library",
                    "group" to module.group,
                    "name" to artifact.name,
                    "version" to module.version,
                    "purl" to "pkg:maven/${module.group}/${artifact.name}@${module.version}",
                )
            }
            .distinctBy { it["purl"] }
            .sortedBy { it["purl"] }

        val bom = linkedMapOf(
            "bomFormat" to "CycloneDX",
            "specVersion" to "1.6",
            "serialNumber" to "urn:uuid:00000000-0000-0000-0000-000000000000",
            "version" to 1,
            "metadata" to linkedMapOf(
                "component" to linkedMapOf(
                    "type" to "application",
                    "group" to project.group.toString(),
                    "name" to project.name,
                    "version" to project.version.toString(),
                ),
            ),
            "components" to components,
        )

        val output = reportFile.get().asFile
        output.parentFile.mkdirs()
        output.writeText(JsonOutput.prettyPrint(JsonOutput.toJson(bom)) + System.lineSeparator())
        logger.lifecycle("Wrote ${components.size} runtime components to ${output.path}")
    }
}
