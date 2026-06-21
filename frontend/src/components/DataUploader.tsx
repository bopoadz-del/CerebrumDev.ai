import { useCallback, useEffect, useState } from 'react';
import { Upload, FileText, AlertCircle, CheckCircle, Loader2 } from 'lucide-react';
import { uploadFiles, getUploadStatus, getUploadResult } from '../api/client';

interface DataUploaderProps {
  sessionId: string;
  onComplete?: () => void;
}

interface FileStatus {
  file: File;
  status: 'queued' | 'uploading' | 'done' | 'error';
}

const DataUploader: React.FC<DataUploaderProps> = ({ sessionId, onComplete }) => {
  const [files, setFiles] = useState<FileStatus[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadState, setUploadState] = useState({
    status: 'idle',
    progress: 0,
    total_chunks: 0,
    failed_files: [] as string[],
    message: '',
  });
  const [polling, setPolling] = useState(false);

  const pollStatus = useCallback(async () => {
    try {
      const res = await getUploadStatus(sessionId);
      setUploadState(res.data);
      if (res.data.status === 'completed' || res.data.status === 'completed_with_warnings') {
        setPolling(false);
        getUploadResult(sessionId).catch(console.error);
        onComplete?.();
      } else if (res.data.status === 'failed') {
        setPolling(false);
      }
    } catch (err) {
      console.error(err);
    }
  }, [sessionId, onComplete]);

  useEffect(() => {
    if (!polling) return;
    const id = setInterval(pollStatus, 2000);
    return () => clearInterval(id);
  }, [polling, pollStatus]);

  const handleFiles = (incoming: FileList | null) => {
    if (!incoming) return;
    const newFiles = Array.from(incoming).map(f => ({ file: f, status: 'queued' as const }));
    setFiles(prev => [...prev, ...newFiles]);
  };

  const handleUpload = async () => {
    if (files.length === 0) return;
    setFiles(prev => prev.map(f => ({ ...f, status: 'uploading' })));
    try {
      await uploadFiles(
        sessionId,
        files.map(f => f.file)
      );
      setFiles(prev => prev.map(f => ({ ...f, status: 'done' })));
      setPolling(true);
    } catch (err) {
      console.error(err);
      setFiles(prev => prev.map(f => ({ ...f, status: 'error' })));
    }
  };

  const statusColor = (status: string) => {
    switch (status) {
      case 'completed':
      case 'completed_with_warnings':
        return 'text-green-600';
      case 'failed':
        return 'text-red-600';
      case 'processing':
      case 'pending':
        return 'text-blue-600';
      default:
        return 'text-gray-600';
    }
  };

  return (
    <div className="bg-white rounded-lg shadow p-6 space-y-6">
      <h2 className="text-xl font-semibold">Phase 2: Upload & Index Documents</h2>
      <p className="text-gray-600">
        Drop PDFs, images, Word docs, or text files. They will be parsed, chunked, embedded, and indexed.
      </p>

      <div
        onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={e => { e.preventDefault(); setIsDragging(false); handleFiles(e.dataTransfer.files); }}
        className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
          isDragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300'
        }`}
      >
        <Upload className="w-10 h-10 text-gray-400 mx-auto mb-3" />
        <p className="text-sm text-gray-600">
          Drag & drop files here, or{' '}
          <label className="text-blue-600 cursor-pointer hover:underline">
            browse
            <input
              type="file"
              multiple
              className="hidden"
              onChange={e => handleFiles(e.target.files)}
            />
          </label>
        </p>
      </div>

      {files.length > 0 && (
        <div className="space-y-2">
          {files.map((item, idx) => (
            <div key={idx} className="flex items-center justify-between p-2 border rounded">
              <div className="flex items-center space-x-2">
                <FileText className="w-4 h-4 text-gray-500" />
                <span className="text-sm">{item.file.name}</span>
              </div>
              {item.status === 'uploading' && <Loader2 className="w-4 h-4 animate-spin text-blue-600" />}
              {item.status === 'done' && <CheckCircle className="w-4 h-4 text-green-600" />}
              {item.status === 'error' && <AlertCircle className="w-4 h-4 text-red-600" />}
            </div>
          ))}
        </div>
      )}

      <button
        onClick={handleUpload}
        disabled={files.length === 0 || uploadState.status === 'processing' || uploadState.status === 'pending'}
        className="w-full py-2 px-4 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
      >
        {uploadState.status === 'processing' ? 'Indexing...' : uploadState.status === 'pending' ? 'Uploading...' : 'Start Indexing'}
      </button>

      {(uploadState.status !== 'idle' || polling) && (
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span className={statusColor(uploadState.status)}>
              {uploadState.status === 'idle' ? 'Starting...' : uploadState.status.replace(/_/g, ' ')}
            </span>
            <span className="text-gray-600">{Math.round(uploadState.progress * 100)}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div
              className="bg-blue-600 h-2 rounded-full transition-all"
              style={{ width: `${Math.max(uploadState.progress * 100, 5)}%` }}
            />
          </div>
          {uploadState.message && <p className="text-sm text-gray-600">{uploadState.message}</p>}
          {uploadState.total_chunks > 0 && (
            <p className="text-sm text-gray-600">{uploadState.total_chunks} chunks indexed</p>
          )}
          {uploadState.failed_files.length > 0 && (
            <div className="text-sm text-red-600">
              Failed files: {uploadState.failed_files.join(', ')}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default DataUploader;
