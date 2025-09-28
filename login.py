from flask import Flask, send_file
import qrcode
import io

app = Flask(__name__)

@app.route("/get-qr")
def get_qr():
    qr_string = "your-qr-code-text-from-go"
    qr_img = qrcode.make(qr_string)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

app.run(port=5000)
