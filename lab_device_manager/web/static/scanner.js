(function (root) {
  let stream = null;
  let frameHandle = null;
  let timerHandle = null;
  let active = false;

  async function stop() {
    if (frameHandle) cancelAnimationFrame(frameHandle);
    if (timerHandle) clearTimeout(timerHandle);
    frameHandle = null;
    timerHandle = null;
    active = false;
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
  }

  async function finish(value, dialog, options) {
    if (!active) return;
    await stop();
    dialog.close();
    if (options.onResult) await options.onResult(value);
  }

  async function decodeOnServer(video) {
    const canvas = document.createElement("canvas");
    const sourceWidth = video.videoWidth || 1280;
    const sourceHeight = video.videoHeight || 720;
    const scale = Math.min(1, 960 / sourceWidth);
    canvas.width = Math.max(1, Math.round(sourceWidth * scale));
    canvas.height = Math.max(1, Math.round(sourceHeight * scale));
    canvas.getContext("2d", {alpha:false}).drawImage(
      video, 0, 0, canvas.width, canvas.height
    );
    const blob = await new Promise(resolve =>
      canvas.toBlob(resolve, "image/jpeg", 0.72)
    );
    if (!blob) return null;
    const form = new FormData();
    form.append("image", blob, "camera-frame.jpg");
    const response = await fetch("/api/scan/decode", {
      method:"POST",
      body:form
    });
    if (response.status === 422) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "识码服务不可用");
    return data.code || null;
  }

  async function open(options = {}) {
    await stop();
    const dialog = document.getElementById("cameraScanDialog");
    const video = document.getElementById("cameraScanVideo");
    const status = document.getElementById("cameraScanStatus");
    const manual = document.getElementById("cameraScanManual");
    if (!dialog || !video) throw new Error("扫码组件尚未加载");
    manual.value = "";
    active = true;
    status.textContent = "正在打开后置摄像头…";
    dialog.showModal();
    const detector = "BarcodeDetector" in root
      ? new BarcodeDetector({
          formats:["qr_code","code_128","ean_13","ean_8","data_matrix"]
        })
      : null;
    if (!navigator.mediaDevices?.getUserMedia) {
      status.textContent = "当前地址不能调用摄像头，请使用 HTTPS，或用系统相机扫描标签。";
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video:{facingMode:{ideal:"environment"}},
        audio:false
      });
      video.srcObject = stream;
      await video.play();
      status.textContent = detector
        ? "请将二维码或条码放入画面中央"
        : "正在通过局域网识别画面中的二维码或条码";
      if (!detector) {
        const scanServerFrame = async () => {
          if (!active) return;
          try {
            const value = await decodeOnServer(video);
            if (value) {
              await finish(value, dialog, options);
              return;
            }
          } catch (error) {
            status.textContent = `识码暂不可用：${error.message}`;
          }
          timerHandle = setTimeout(scanServerFrame, 450);
        };
        timerHandle = setTimeout(scanServerFrame, 250);
        return;
      }
      const scanFrame = async () => {
        if (!active) return;
        try {
          const codes = await detector.detect(video);
          if (codes.length) {
            await finish(codes[0].rawValue, dialog, options);
            return;
          }
        } catch (_) {}
        frameHandle = requestAnimationFrame(scanFrame);
      };
      frameHandle = requestAnimationFrame(scanFrame);
    } catch (error) {
      await stop();
      status.textContent = `摄像头不可用：${error.message}`;
    }
  }

  async function submitManual() {
    const input = document.getElementById("cameraScanManual");
    const value = input.value.trim();
    if (!value) return;
    const dialog = document.getElementById("cameraScanDialog");
    await stop();
    dialog.close();
    if (root.PuricoreScanner.onResult) {
      await root.PuricoreScanner.onResult(value);
    }
  }

  root.PuricoreScanner = {
    open(options = {}) {
      this.onResult = options.onResult || null;
      return open(options);
    },
    stop,
    submitManual
  };
})(window);
