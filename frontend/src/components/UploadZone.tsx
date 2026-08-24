import { useRef, useState } from "react";
import { useI18n } from "../i18n";

interface Props {
  onUpload: (files: File[]) => void;
  busy: boolean;
}

export function UploadZone({ onUpload, busy }: Props) {
  const { t, tf } = useI18n();
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
          ? tf("upload.selected", {
              n: picked.length,
              s: picked.length > 1 ? "s" : "",
            })
          : busy
            ? t("upload.processing")
            : t("upload.title")}
      </p>
      <p className="muted small">{t("upload.hint")}</p>
    </section>
  );
}
