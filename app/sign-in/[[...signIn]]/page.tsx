import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <main className="authPage">
      <div className="breath" aria-hidden="true" />
      <div className="vignette" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />
      <div className="authBrand">
        <span className="mark" />
        <span>June</span>
      </div>
      <SignIn
        path="/sign-in"
        routing="path"
        forceRedirectUrl="/talk"
        fallbackRedirectUrl="/talk"
      />
    </main>
  );
}
