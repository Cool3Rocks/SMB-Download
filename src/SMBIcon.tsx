import type { SVGProps } from "react";

export default function SMBIcon({ width = 32, height = 32, monochrome = false, ...props }: SVGProps<SVGSVGElement> & { monochrome?: boolean }) {
  if (monochrome) return <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width={width} height={height}
    role="img" aria-label="SMB Download" focusable="false" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" {...props}>
    <path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
    <path d="M12 10v7m-3.5-3.5L12 17l3.5-3.5" />
  </svg>;
  return <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width={width} height={height}
    role="img" aria-label="SMB Download" focusable="false" {...props}>
    <rect x="2" y="2" width="60" height="60" rx="15" fill="#102A43" />
    <path d="M12 23a5 5 0 0 1 5-5h11l6 6h13a5 5 0 0 1 5 5v20a5 5 0 0 1-5 5H17a5 5 0 0 1-5-5Z"
      fill="#259FD2" />
    <path d="M12 30h40v19a5 5 0 0 1-5 5H17a5 5 0 0 1-5-5Z" fill="#40C6C0" />
    <path d="M32 31v13m-7-6 7 7 7-7" fill="none" stroke="#FFFFFF" strokeWidth="4.5"
      strokeLinecap="round" strokeLinejoin="round" />
    <path d="M24 50h16" stroke="#102A43" strokeWidth="3" strokeLinecap="round" />
    <circle cx="45" cy="17" r="4" fill="#40C6C0" />
    <path d="M45 21v3" stroke="#40C6C0" strokeWidth="3" />
  </svg>;
}
