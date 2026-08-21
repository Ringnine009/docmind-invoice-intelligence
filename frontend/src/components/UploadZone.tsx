import { useRef, useState } from "react";

interface Props {
  onUpload: (files: File[]) => void;
  busy: boolean;
}

export function UploadZone({ onUpload, busy }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [picked, setPicked] = useState<File[]>([]);

  function handleFiles(list: FileList | null) {
    if (!list) return;
    const files = Array.from(list).filter((f) =>
      f.name.toLowerCase().endsWith(".pdf")
    );
    if (files.length === 0) return;
    setPicked(files);
    onUpload(files);
  }

  return (
    <section
      className={`upload ${dragging ? "dragging" : ""}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        handleFiles(e.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        multiple
        hidden
        onChange={(e) => handleFiles(e.target.files)}
      />
      <div className="upload-icon">⇪</div>
      <p className="upload-title">
        {picked.length > 0 && !busy
          ? `${picked.length} PDF${picked.length > 1 ? "s" : ""} selected — processing…`
          : busy
            ? "Processing…"
            : "Drop invoice PDFs here or click to browse"}
      </p>
      <p className="muted small">single or batch · rendered at 200 dpi · Qwen vision extraction</p>
    </section>
  );
}
