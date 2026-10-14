/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  // Le replay de démonstration (mode maquette) est lu sur disque par la route /replay-data.
  outputFileTracingIncludes: {
    "/replay-data/[id]": ["./fixtures/**/*"],
  },
};

export default nextConfig;
