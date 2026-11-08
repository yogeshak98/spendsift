export function Pill(props: { text: string; kind?: "cat" | "unk" | "ok" | "short"; title?: string }) {
  return (
    <span className={`pill ${props.kind ?? "cat"}`} title={props.title}>
      {props.text}
    </span>
  );
}
