import { random } from 'monitor-common/utils/utils';
import { Component, Emit, Prop } from 'vue-property-decorator';
import { Component as tsc } from 'vue-tsx-support';

import './config-map-upload.scss';

interface IConfigMapFile {
  uid: string;
  filename: string;
  content: string;
}

interface IUploadFile {
  name: string;
  uid: string;
  status: string;
  done: boolean;
  progress: string;
}

interface IProps {
  value?: IConfigMapFile[];
  multiple?: boolean;
  accept?: string;
}

interface IEvents {
  onChange: (files: IConfigMapFile[]) => void;
}

@Component({ name: 'ConfigMapUpload' })
export default class ConfigMapUpload extends tsc<IProps, IEvents> {
  @Prop({ type: Array, default: () => [] }) value: IConfigMapFile[];
  @Prop({ type: Boolean, default: true }) multiple: boolean;
  @Prop({ type: String, default: '.yaml,.yml' }) accept: string;

  files: IConfigMapFile[] = [];
  initialFiles: IUploadFile[] = [];

  get maxSize() {
    return +this.$store.getters.collectingConfigFileMaxSize || 5;
  }

  get tip() {
    return this.$t('支持同时上传多个ConfigMap配置');
  }

  created() {
    this.files = Array.isArray(this.value) ? [...this.value] : [];
    this.initialFiles = this.files.map(file => ({
      name: file.filename,
      uid: file.uid,
      status: 'done',
      done: true,
      progress: '100%',
    }));
  }

  @Emit('change')
  emitChange(files: IConfigMapFile[]) {
    return files;
  }

  handleCustomRequest(options) {
    const { fileObj, onSuccess, onDone } = options;
    const reader = new FileReader();
    reader.onload = () => {
      fileObj.uid = random(32);
      fileObj.content = reader.result;
      onSuccess({ code: 0 }, fileObj);
      onDone(fileObj);
    };
    reader.onerror = () => {
      fileObj.errorMsg = this.$t('解析文件失败');
      onDone(fileObj);
    };
    reader.readAsText(fileObj.origin, 'UTF-8');
  }

  handleSuccess(file) {
    if (!file.uid) return;
    this.files = [...this.files, { uid: file.uid, filename: file.name, content: file.content }];
    this.emitChange(this.files);
  }

  handleDelete(file) {
    if (!file.uid) return;
    this.files = this.files.filter(item => item.uid !== file.uid);
    this.emitChange(this.files);
  }

  render() {
    return (
      <div class='config-map-upload'>
        <bk-upload
          class='config-map-upload-control'
          accept={this.accept}
          custom-request={this.handleCustomRequest}
          files={this.initialFiles}
          multiple={this.multiple}
          size={this.maxSize}
          theme='draggable'
          tip={this.tip}
          on={{
            'on-success': this.handleSuccess,
            'on-delete': this.handleDelete,
          }}
        />
      </div>
    );
  }
}
