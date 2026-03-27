/** @type {import('next').NextConfig} */
const nextConfig = {
  typescript: {
    ignoreBuildErrors: false,
  },
  images: {
    unoptimized: true,
  },
  // Intentional: local dev LAN IP so the War Room can be accessed from other
  // devices on the home network during development. Update if the dev machine
  // IP changes; this has no effect in production builds.
  allowedDevOrigins: ['192.168.1.136'],
}

export default nextConfig
