"use client";

import { useState } from "react";

import { CheckIcon, CopyIcon } from "@radix-ui/react-icons";
import { CopyToClipboard } from "react-copy-to-clipboard";

interface CopyButtonProps {
  ariaLabel: string;
  textToCopy: string;
}

const CopyButton: React.FC<CopyButtonProps> = ({ ariaLabel, textToCopy }) => {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    setCopied(true);
    setTimeout(() => {
      setCopied(false);
    }, 2000);
  };

  return (
    <CopyToClipboard text={textToCopy} onCopy={handleCopy}>
      <button
        aria-label={ariaLabel}
        type="button"
        className="absolute top-2 right-2 bg-white hover:bg-gray-100 p-2 text-black rounded-md"
      >
        {copied ? (
          <CheckIcon aria-hidden="true" className="size-3" />
        ) : (
          <CopyIcon aria-hidden="true" className="size-3" />
        )}
        <span aria-live="polite" className="sr-only" role="status">
          {copied ? `${ariaLabel}: copied.` : ""}
        </span>
      </button>
    </CopyToClipboard>
  );
};

export default CopyButton;
