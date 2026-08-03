import { clerkMiddleware } from "@clerk/nextjs/server";

// Next 16 renamed the middleware convention to "proxy".
// The page itself stays public; the voice session is gated by the API, which
// verifies the Clerk token independently rather than trusting the browser.
export default clerkMiddleware();

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
