import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

export type PresencePhase = "entering" | "entered" | "exiting";

interface Props {
  present: boolean;
  children: (phase: PresencePhase) => ReactNode;
  exitDuration?: number;
  onEnterComplete?: () => void;
  onExitComplete?: () => void;
}

export default function Presence({ present, children, exitDuration = 130, onEnterComplete, onExitComplete }: Props) {
  const [rendered, setRendered] = useState(present);
  const [phase, setPhase] = useState<PresencePhase>(present ? "entering" : "exiting");
  const renderedRef = useRef(present);
  const onEnterCompleteRef = useRef(onEnterComplete);
  const onExitCompleteRef = useRef(onExitComplete);

  useEffect(() => {
    onEnterCompleteRef.current = onEnterComplete;
    onExitCompleteRef.current = onExitComplete;
  }, [onEnterComplete, onExitComplete]);

  useEffect(() => {
    if (present && rendered && phase === "entered") onEnterCompleteRef.current?.();
  }, [phase, present, rendered]);

  useEffect(() => {
    let firstFrame = 0;
    let secondFrame = 0;
    let exitTimer = 0;

    if (present) {
      renderedRef.current = true;
      setRendered(true);
      setPhase("entering");
      firstFrame = window.requestAnimationFrame(() => {
        secondFrame = window.requestAnimationFrame(() => setPhase("entered"));
      });
    } else if (renderedRef.current) {
      setPhase("exiting");
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      exitTimer = window.setTimeout(() => {
        renderedRef.current = false;
        setRendered(false);
        onExitCompleteRef.current?.();
      }, reducedMotion ? 0 : exitDuration);
    }

    return () => {
      window.cancelAnimationFrame(firstFrame);
      window.cancelAnimationFrame(secondFrame);
      window.clearTimeout(exitTimer);
    };
  }, [exitDuration, present]);

  return rendered ? <>{children(phase)}</> : null;
}
