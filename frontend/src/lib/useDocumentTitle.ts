import { useEffect } from "react";

const SUFFIX = " · Grain Conference Intel";

/** Set the browser tab title for the current page. */
export function useDocumentTitle(title: string) {
  useEffect(() => {
    const prev = document.title;
    document.title = title ? `${title}${SUFFIX}` : `Grain Conference Intel`;
    return () => {
      document.title = prev;
    };
  }, [title]);
}
