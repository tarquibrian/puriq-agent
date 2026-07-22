import { defineConfig } from "astro/config";

// Sitio estatico: ideal para hosting en AWS Amplify / S3 + CloudFront.
export default defineConfig({
  output: "static",
  build: { assets: "_assets" },
});
