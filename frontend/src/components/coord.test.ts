import { describe, expect, it } from "vitest";
import { coordPayload, emptyCoord } from "./CoordinateInput";

describe("coordPayload", () => {
  it("WGS84 : transmet les chaînes telles quelles (DMS accepté côté serveur)", () => {
    expect(coordPayload({ ...emptyCoord, lat: "31°30'N", lon: "-9,77" })).toEqual({ lat: "31°30'N", lon: "-9,77", crs: "EPSG:4326" });
  });
  it("UTM : construit l'alias de zone", () => {
    expect(coordPayload({ ...emptyCoord, mode: "utm", utmZone: 28, x: "403148,7", y: "2621335.8" })).toEqual({
      x: 403148.7,
      y: 2621335.8,
      crs: "UTM28N",
    });
  });
  it("saisie incomplète → null", () => {
    expect(coordPayload({ ...emptyCoord, mode: "lambert", x: "1" })).toBeNull();
    expect(coordPayload({ ...emptyCoord, lat: "31" })).toBeNull();
  });
});
