// @ts-nocheck
import React from 'react';

interface RainbowButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  className?: string;
}

/**
 * RainbowButton — animated Perseus-palette rainbow border button.
 * Gradient: cyan → teal → mint → gold (replaces garish neon defaults).
 */
export const RainbowButton: React.FC<RainbowButtonProps> = ({ children, onClick, className = '' }) => {
  return (
    <div className="relative inline-flex">
      <button
        onClick={onClick}
        className={`rainbow-btn relative inline-flex items-center justify-center gap-2 px-4 h-10 rounded-xl bg-[#0a0a0f] border-none text-white cursor-pointer text-xs font-semibold tracking-wide transition-all duration-200 hover:translate-y-[-1px] active:translate-y-0 ${className}`}
      >
        {children}
      </button>

      <style jsx>{`
        .rainbow-btn::before,
        .rainbow-btn::after {
          content: '';
          position: absolute;
          left: -2px;
          top: -2px;
          border-radius: 14px;
          background: linear-gradient(
            45deg,
            #58e0ff,
            #39f3e2,
            #62f1b5,
            #ffb347,
            #58e0ff,
            #39f3e2,
            #62f1b5,
            #ffb347
          );
          background-size: 400%;
          width: calc(100% + 4px);
          height: calc(100% + 4px);
          z-index: -1;
          animation: perseus-rainbow 20s linear infinite;
        }
        .rainbow-btn::after {
          filter: blur(12px);
          opacity: 0.5;
        }
        @keyframes perseus-rainbow {
          0% { background-position: 0 0; }
          50% { background-position: 400% 0; }
          100% { background-position: 0 0; }
        }
      `}</style>
    </div>
  );
};

// Legacy demo export kept for compatibility
export const Button = RainbowButton;
