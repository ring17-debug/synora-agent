#![allow(dead_code)]

use std::{
    io::{Read, Write},
    net::TcpStream,
    time::Duration,
};

#[derive(Debug)]
pub enum RpcClientError {
    Io(std::io::Error),
    InvalidResponse(String),
    HttpStatus(u16, String),
    InvalidJson(String),
}

impl std::fmt::Display for RpcClientError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "I/O error: {}", error),

            Self::InvalidResponse(message) => {
                write!(formatter, "invalid response: {}", message)
            }

            Self::HttpStatus(status, body) => {
                write!(formatter, "RPC returned HTTP {}: {}", status, body)
            }

            Self::InvalidJson(message) => {
                write!(formatter, "invalid JSON: {}", message)
            }
        }
    }
}

impl std::error::Error for RpcClientError {}

impl From<std::io::Error> for RpcClientError {
    fn from(error: std::io::Error) -> Self {
        Self::Io(error)
    }
}

#[derive(Debug, Clone)]
pub struct RpcClient {
    address: String,
    timeout: Duration,
}

impl RpcClient {
    pub fn new(address: impl Into<String>) -> Self {
        Self {
            address: normalize_address(&address.into()),
            timeout: Duration::from_secs(10),
        }
    }

    pub fn with_timeout(address: impl Into<String>, timeout: Duration) -> Self {
        Self {
            address: normalize_address(&address.into()),
            timeout,
        }
    }

    pub fn address(&self) -> &str {
        &self.address
    }

    // ------------------------------------------------------------
    // Basic RPC endpoints
    // ------------------------------------------------------------

    pub fn get_root(&self) -> Result<String, RpcClientError> {
        self.get("/")
    }

    pub fn get_status(&self) -> Result<String, RpcClientError> {
        self.get("/status")
    }

    pub fn get_mempool(&self) -> Result<String, RpcClientError> {
        self.get("/mempool")
    }

    pub fn get_transaction(&self, hash: &str) -> Result<String, RpcClientError> {
        validate_hash(hash)?;

        let normalized_hash = strip_0x(hash);

        self.get(&format!("/transaction/{}", normalized_hash))
    }

    // ------------------------------------------------------------
    // Future / optional block endpoints
    //
    // These methods are kept here so the client API can grow with
    // the RPC server. If the server does not expose the route yet,
    // the server will correctly return HTTP 404.
    // ------------------------------------------------------------

    pub fn get_latest_block(&self) -> Result<String, RpcClientError> {
        self.get("/block/latest")
    }

    pub fn get_block(&self, height: u64) -> Result<String, RpcClientError> {
        self.get(&format!("/block/{}", height))
    }

    pub fn get_account(&self, address: &str) -> Result<String, RpcClientError> {
        validate_address(address)?;

        let normalized_address = strip_0x(address);

        self.get(&format!("/state/{}", normalized_address))
    }

    // ------------------------------------------------------------
    // Transaction submission
    // ------------------------------------------------------------

    #[allow(clippy::too_many_arguments)]
    pub fn submit_transaction(
        &self,
        chain_id: u64,
        nonce: u64,
        sender: &str,
        recipient: &str,
        value: u64,
        gas_limit: u64,
        gas_price: u64,
        data: &str,
        public_key: &str,
        signature: &str,
    ) -> Result<String, RpcClientError> {
        validate_address(sender)?;
        validate_address(recipient)?;
        validate_hex(data)?;
        validate_fixed_hex(public_key, 32, "public key")?;
        validate_fixed_hex(signature, 64, "signature")?;

        let sender = strip_0x(sender);
        let recipient = strip_0x(recipient);
        let data = strip_0x(data);
        let public_key = strip_0x(public_key);
        let signature = strip_0x(signature);

        let body = format!(
            concat!(
                "{{",
                "\"chain_id\":{},",
                "\"nonce\":{},",
                "\"sender\":\"{}\",",
                "\"recipient\":\"{}\",",
                "\"value\":{},",
                "\"gas_limit\":{},",
                "\"gas_price\":{},",
                "\"data\":\"{}\",",
                "\"public_key\":\"{}\",",
                "\"signature\":\"{}\"",
                "}}"
            ),
            chain_id,
            nonce,
            json_escape(sender),
            json_escape(recipient),
            value,
            gas_limit,
            gas_price,
            json_escape(data),
            json_escape(public_key),
            json_escape(signature),
        );

        self.post("/transaction", &body)
    }

    // ------------------------------------------------------------
    // Block production
    // ------------------------------------------------------------

    pub fn produce_block(&self) -> Result<String, RpcClientError> {
        self.post("/block/produce", "")
    }

    // ------------------------------------------------------------
    // Internal HTTP helpers
    // ------------------------------------------------------------

    fn get(&self, path: &str) -> Result<String, RpcClientError> {
        self.request("GET", path, "")
    }

    fn post(&self, path: &str, body: &str) -> Result<String, RpcClientError> {
        self.request("POST", path, body)
    }

    fn request(&self, method: &str, path: &str, body: &str) -> Result<String, RpcClientError> {
        let mut stream = TcpStream::connect(&self.address)?;

        stream.set_read_timeout(Some(self.timeout))?;
        stream.set_write_timeout(Some(self.timeout))?;

        let request = format!(
            "{} {} HTTP/1.1\r\n\
             Host: {}\r\n\
             Accept: application/json\r\n\
             Content-Type: application/json\r\n\
             Content-Length: {}\r\n\
             Connection: close\r\n\
             \r\n\
             {}",
            method,
            path,
            self.address,
            body.len(),
            body,
        );

        stream.write_all(request.as_bytes())?;
        stream.flush()?;

        /*
         * Kita menutup sisi WRITE setelah request selesai.
         *
         * Server tetap dapat membaca seluruh request,
         * sementara client masih dapat membaca response.
         */
        stream.shutdown(std::net::Shutdown::Write)?;

        let mut response = Vec::new();

        stream.read_to_end(&mut response)?;

        parse_http_response(&response)
    }
}

// ================================================================
// Address / hexadecimal validation
// ================================================================

fn normalize_address(address: &str) -> String {
    address
        .strip_prefix("http://")
        .or_else(|| address.strip_prefix("https://"))
        .unwrap_or(address)
        .trim_end_matches('/')
        .to_string()
}

fn strip_0x(value: &str) -> &str {
    value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
        .unwrap_or(value)
}

fn validate_address(address: &str) -> Result<(), RpcClientError> {
    let value = strip_0x(address);

    if value.len() != 40 {
        return Err(RpcClientError::InvalidResponse(
            "address must contain exactly 20 bytes".to_string(),
        ));
    }

    if !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(RpcClientError::InvalidResponse(
            "address contains invalid hexadecimal characters".to_string(),
        ));
    }

    Ok(())
}

fn validate_hash(hash: &str) -> Result<(), RpcClientError> {
    let value = strip_0x(hash);

    if value.len() != 64 {
        return Err(RpcClientError::InvalidResponse(
            "transaction hash must contain exactly 32 bytes".to_string(),
        ));
    }

    if !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(RpcClientError::InvalidResponse(
            "transaction hash contains invalid hexadecimal characters".to_string(),
        ));
    }

    Ok(())
}

fn validate_fixed_hex(
    value: &str,
    expected_bytes: usize,
    field: &str,
) -> Result<(), RpcClientError> {
    let value = strip_0x(value);

    if value.len() != expected_bytes * 2 {
        return Err(RpcClientError::InvalidResponse(format!(
            "{} must contain exactly {} bytes",
            field, expected_bytes
        )));
    }

    if !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(RpcClientError::InvalidResponse(format!(
            "{} contains invalid hexadecimal characters",
            field
        )));
    }

    Ok(())
}

fn validate_hex(value: &str) -> Result<(), RpcClientError> {
    let value = strip_0x(value);

    if !value.len().is_multiple_of(2) {
        return Err(RpcClientError::InvalidResponse(
            "hex data must contain an even number of characters".to_string(),
        ));
    }

    if !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(RpcClientError::InvalidResponse(
            "hex data contains invalid characters".to_string(),
        ));
    }

    Ok(())
}

// ================================================================
// HTTP response parser
// ================================================================

fn parse_http_response(response: &[u8]) -> Result<String, RpcClientError> {
    let header_end = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|position| position + 4)
        .or_else(|| {
            response
                .windows(2)
                .position(|window| window == b"\n\n")
                .map(|position| position + 2)
        })
        .ok_or_else(|| {
            RpcClientError::InvalidResponse("HTTP header terminator not found".to_string())
        })?;

    let header = std::str::from_utf8(&response[..header_end])
        .map_err(|_| RpcClientError::InvalidResponse("HTTP header is not UTF-8".to_string()))?;

    let status_line = header.lines().next().ok_or_else(|| {
        RpcClientError::InvalidResponse("HTTP status line is missing".to_string())
    })?;

    let mut status_parts = status_line.split_whitespace();

    let http_version = status_parts
        .next()
        .ok_or_else(|| RpcClientError::InvalidResponse("HTTP version is missing".to_string()))?;

    if http_version != "HTTP/1.1" && http_version != "HTTP/1.0" {
        return Err(RpcClientError::InvalidResponse(
            "unsupported HTTP version".to_string(),
        ));
    }

    let status = status_parts
        .next()
        .ok_or_else(|| RpcClientError::InvalidResponse("HTTP status is missing".to_string()))?
        .parse::<u16>()
        .map_err(|_| RpcClientError::InvalidResponse("HTTP status is invalid".to_string()))?;

    let body = String::from_utf8(response[header_end..].to_vec())
        .map_err(|_| RpcClientError::InvalidResponse("HTTP body is not UTF-8".to_string()))?;

    if !(200..300).contains(&status) {
        return Err(RpcClientError::HttpStatus(status, body));
    }

    Ok(body)
}

// ================================================================
// JSON helpers
// ================================================================

fn json_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}

// ================================================================
// Tests
// ================================================================

#[cfg(test)]
mod tests {
    use super::*;

    use std::{
        io::{Read, Write},
        net::TcpListener,
        thread,
    };

    fn start_mock_server(response: &'static str) -> (String, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("mock server should bind");

        let address = listener
            .local_addr()
            .expect("mock server address should exist")
            .to_string();

        let handle = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("mock server should accept");

            let mut request = Vec::new();

            stream
                .read_to_end(&mut request)
                .expect("mock server should read request");

            stream
                .write_all(response.as_bytes())
                .expect("mock server should write response");
        });

        (address, handle)
    }

    #[test]
    fn address_is_normalized() {
        assert_eq!(
            normalize_address("http://127.0.0.1:8080/"),
            "127.0.0.1:8080"
        );

        assert_eq!(normalize_address("127.0.0.1:8080"), "127.0.0.1:8080");

        assert_eq!(
            normalize_address("https://127.0.0.1:8080/"),
            "127.0.0.1:8080"
        );
    }

    #[test]
    fn address_validation_accepts_valid_address() {
        assert!(validate_address("00112233445566778899aabbccddeeff00112233").is_ok());

        assert!(validate_address("0x00112233445566778899aabbccddeeff00112233").is_ok());
    }

    #[test]
    fn address_validation_rejects_invalid_address() {
        assert!(validate_address("1234").is_err());

        assert!(validate_address("zz112233445566778899aabbccddeeff00112233").is_err());
    }

    #[test]
    fn hash_validation_accepts_valid_hash() {
        assert!(
            validate_hash("0000000000000000000000000000000000000000000000000000000000000000")
                .is_ok()
        );

        assert!(validate_hash(
            "0x0000000000000000000000000000000000000000000000000000000000000000"
        )
        .is_ok());
    }

    #[test]
    fn hash_validation_rejects_invalid_hash() {
        assert!(validate_hash("1234").is_err());

        assert!(
            validate_hash("zz00000000000000000000000000000000000000000000000000000000000000")
                .is_err()
        );
    }

    #[test]
    fn hex_validation_accepts_valid_data() {
        assert!(validate_hex("").is_ok());
        assert!(validate_hex("00").is_ok());
        assert!(validate_hex("010203").is_ok());
        assert!(validate_hex("0x010203").is_ok());
    }

    #[test]
    fn hex_validation_rejects_invalid_data() {
        assert!(validate_hex("0").is_err());
        assert!(validate_hex("xyz").is_err());
    }

    #[test]
    fn public_key_validation_accepts_32_bytes() {
        assert!(validate_fixed_hex(&"aa".repeat(32), 32, "public key").is_ok());
    }

    #[test]
    fn public_key_validation_rejects_wrong_length() {
        assert!(validate_fixed_hex("aa", 32, "public key").is_err());
    }

    #[test]
    fn signature_validation_accepts_64_bytes() {
        assert!(validate_fixed_hex(&"aa".repeat(64), 64, "signature").is_ok());
    }

    #[test]
    fn signature_validation_rejects_wrong_length() {
        assert!(validate_fixed_hex("aa", 64, "signature").is_err());
    }

    #[test]
    fn successful_response_is_parsed() {
        let response = "HTTP/1.1 200 OK\r\n\
             Content-Type: application/json\r\n\
             Content-Length: 15\r\n\
             Connection: close\r\n\
             \r\n\
             {\"status\":\"ok\"}";

        let body = parse_http_response(response.as_bytes()).expect("response should parse");

        assert_eq!(body, "{\"status\":\"ok\"}");
    }

    #[test]
    fn error_status_is_returned() {
        let response = "HTTP/1.1 404 Not Found\r\n\
             Content-Type: application/json\r\n\
             Content-Length: 19\r\n\
             Connection: close\r\n\
             \r\n\
             {\"status\":\"error\"}";

        let result = parse_http_response(response.as_bytes());

        match result {
            Err(RpcClientError::HttpStatus(status, body)) => {
                assert_eq!(status, 404);
                assert_eq!(body, "{\"status\":\"error\"}");
            }

            other => {
                panic!("unexpected result: {:?}", other);
            }
        }
    }

    #[test]
    fn successful_rpc_request_is_parsed() {
        let response = "HTTP/1.1 200 OK\r\n\
             Content-Type: application/json\r\n\
             Content-Length: 15\r\n\
             Connection: close\r\n\
             \r\n\
             {\"status\":\"ok\"}";

        let (address, handle) = start_mock_server(response);

        let client = RpcClient::new(address);

        let result = client.get_root().expect("RPC request should succeed");

        assert_eq!(result, "{\"status\":\"ok\"}");

        handle.join().expect("mock server should finish");
    }

    #[test]
    fn transaction_request_contains_expected_fields() {
        let public_key = "aa".repeat(32);
        let signature = "bb".repeat(64);

        let sender = "00112233445566778899aabbccddeeff00112233";

        let recipient = "ffeeddccbbaa99887766554433221100ffeeddcc";

        let body = format!(
            concat!(
                "{{",
                "\"chain_id\":{},",
                "\"nonce\":{},",
                "\"sender\":\"{}\",",
                "\"recipient\":\"{}\",",
                "\"value\":{},",
                "\"gas_limit\":{},",
                "\"gas_price\":{},",
                "\"data\":\"{}\",",
                "\"public_key\":\"{}\",",
                "\"signature\":\"{}\"",
                "}}"
            ),
            1337, 0, sender, recipient, 10_000, 21_000, 1, "010203", public_key, signature,
        );

        assert!(body.contains("\"chain_id\":1337"));
        assert!(body.contains("\"nonce\":0"));
        assert!(body.contains("\"sender\":"));
        assert!(body.contains("\"recipient\":"));
        assert!(body.contains("\"value\":10000"));
        assert!(body.contains("\"gas_limit\":21000"));
        assert!(body.contains("\"gas_price\":1"));
        assert!(body.contains("\"data\":\"010203\""));
        assert!(body.contains("\"public_key\":"));
        assert!(body.contains("\"signature\":"));
    }
}
