/** A small segmented switch: one pressed button among a few. */
export function Segmented<K extends string>(props: {
  label: string;
  options: { key: K; label: string }[];
  value: K;
  onChange: (k: K) => void;
}) {
  return (
    <div className="dash-seg" role="group" aria-label={props.label}>
      {props.options.map((o) => (
        <button
          key={o.key}
          type="button"
          className={o.key === props.value ? "on" : undefined}
          aria-pressed={o.key === props.value}
          onClick={() => props.onChange(o.key)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
