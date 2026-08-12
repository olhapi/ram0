// Modified for Ram0; see NOTICE and repository history.

import Image from "next/image";

export default function ThemeAwareLogo({
  width = 120,
  height = 40,
}: {
  width?: number;
  height?: number;
}) {
  const iconSize = Math.min(height, 32);

  return (
    <div
      aria-label="Ram0"
      className="flex items-center gap-2 text-foreground"
      style={{ width, height }}
    >
      <Image
        src="/images/ram0-mark.svg"
        alt=""
        aria-hidden="true"
        width={iconSize}
        height={iconSize}
      />
      <span className="text-xl font-semibold tracking-tight">Ram0</span>
    </div>
  );
}
