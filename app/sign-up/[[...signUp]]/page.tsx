import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
  return (
    <main className="authPage">
      <div className="breath" aria-hidden="true" />
      <div className="vignette" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />
      <div className="authBrand">
        <span className="mark" />
        <span>Auren</span>
      </div>
      <SignUp
        path="/sign-up"
        routing="path"
        forceRedirectUrl="/talk"
        fallbackRedirectUrl="/talk"
      />
    </main>
  );
}
