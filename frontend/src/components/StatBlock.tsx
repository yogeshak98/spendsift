type Tone = "green" | "red" | "amber" | "blue" | "dim" | "text";

export function StatBlock(props: {
  label: string;
  value: string;
  delta?: string;
  tone?: Tone;
  deltaTone?: Tone;
  // The screen's headline figure: drawn with the accent border.
  hero?: boolean;
}) {
  return (
    <div className={props.hero ? "stat hero" : "stat"}>
      <div className="l">{props.label}</div>
      <div className={`n v-${props.tone ?? "blue"}`}>{props.value}</div>
      {props.delta && (
        <div className={`d v-${props.deltaTone ?? props.tone ?? "dim"}`}>{props.delta}</div>
      )}
    </div>
  );
}
