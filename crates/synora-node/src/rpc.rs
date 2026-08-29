use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    time::Duration,
};

use serde::{Deserialize, Serialize};
use synora_core::{
    block::Block,
    hash::Hash,
    state::Address,
    transaction::{Transaction, PUBLIC_KEY_SIZE, SIGNATURE_SIZE},
};

use crate::node::{NodeError, SynoraNode};

const MAX_RPC_REQUEST_SIZE: usize = 2 * 1024 * 1024;
const MAX_RPC_HEADER_SIZE: usize = 32 * 1024;

#[derive(Debug, Deserialize)]
struct TransactionRequest {
    chain_id: u64,
    nonce: u64,
    sender: String,
    recipient: String,
    value: u64,
    gas_limit: u64,
    gas_price: u64,

    #[serde(default)]
    data: String,

    public_key: String,
    signature: String,
}

#[derive(Debug, Serialize)]
struct RpcResponse<T: Serialize> {
    status: &'static str,
    result: T,
}

#[derive(Debug, Serialize)]
struct RpcErrorResponse {
    status: &'static str,
    error: RpcError,
}

#[derive(Debug, Serialize)]
struct RpcError {
    code: &'static str,
    message: String,
}

#[derive(Debug, Serialize)]
struct RootResponse {
    name: &'static str,
    version: &'static str,
    chain_id: u64,
}

#[derive(Debug, Serialize)]
struct StatusResponse {
    chain_id: u64,
    height: u64,
    pending_transactions: usize,
    latest_block_hash: String,
}

#[derive(Debug, Serialize)]
struct AccountResponse {
    address: String,
    balance: String,
    nonce: u64,
}

#[derive(Debug, Serialize)]
struct TransactionResponse {
    hash: String,
    status: &'static str,
    block_height: Option<u64>,
    transaction: TransactionJson,
}

#[derive(Debug, Serialize)]
struct TransactionJson {
    hash: String,
    chain_id: u64,
    nonce: u64,
    sender: String,
    recipient: String,
    value: u64,
    gas_limit: u64,
    gas_price: u64,
    data: String,
    public_key: String,
    signature: String,
}

#[derive(Debug, Serialize)]
struct BlockResponse {
    hash: String,
    version: u8,
    chain_id: u64,
    height: u64,
    timestamp: u64,
    previous_hash: String,
    state_root: String,
    transactions_root: String,
    transaction_count: usize,
    transactions: Vec<TransactionJson>,
}

pub struct RpcServer {
    address: String,
}

impl RpcServer {
    pub fn new(address: impl Into<String>) -> Self {
        Self {
            address: address.into(),
        }
    }

    pub fn address(&self) -> &str {
        &self.address
    }

    pub fn run(&self, node: &mut SynoraNode) -> std::io::Result<()> {
        let listener = TcpListener::bind(&self.address)?;

        print_server_banner(&self.address);

        run_listener(listener, node)
    }
}

fn print_server_banner(address: &str) {
    println!();
    println!("=================================");
    println!("         SYNORA RPC SERVER       ");
    println!("=================================");
    println!("RPC Address    : http://{}", address);
    println!("Status         : LISTENING");
    println!();
}

fn run_listener(listener: TcpListener, node: &mut SynoraNode) -> std::io::Result<()> {
    for stream in listener.incoming() {
        match stream {
            Ok(mut stream) => {
                if let Err(error) = handle_connection(&mut stream, node) {
                    eprintln!("RPC error: {}", error);
                }
            }

            Err(error) => {
                eprintln!("Connection error: {}", error);
            }
        }
    }

    Ok(())
}

fn read_http_request(stream: &mut TcpStream) -> std::io::Result<String> {
    let mut buffer = Vec::with_capacity(8 * 1024);
    let mut temp = [0u8; 4096];

    let header_end = loop {
        let size = stream.read(&mut temp)?;

        if size == 0 {
            return Ok(String::new());
        }

        if buffer.len() + size > MAX_RPC_REQUEST_SIZE {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "RPC request exceeds maximum size",
            ));
        }

        buffer.extend_from_slice(&temp[..size]);

        if let Some(position) = find_header_end(&buffer) {
            break position;
        }

        if buffer.len() > MAX_RPC_HEADER_SIZE {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "HTTP headers exceed maximum size",
            ));
        }
    };

    let header_bytes = &buffer[..header_end];

    let header_text = std::str::from_utf8(header_bytes).map_err(|_| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "HTTP headers are not valid UTF-8",
        )
    })?;

    let content_length = parse_content_length(header_text)?;

    let body_start = header_end;

    let required_size = body_start.checked_add(content_length).ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "HTTP request size overflow",
        )
    })?;

    if required_size > MAX_RPC_REQUEST_SIZE {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "RPC request exceeds maximum size",
        ));
    }

    while buffer.len() < required_size {
        let size = stream.read(&mut temp)?;

        if size == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "HTTP body ended before Content-Length",
            ));
        }

        if buffer.len() + size > MAX_RPC_REQUEST_SIZE {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "RPC request exceeds maximum size",
            ));
        }

        buffer.extend_from_slice(&temp[..size]);
    }

    buffer.truncate(required_size);

    String::from_utf8(buffer).map_err(|_| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "HTTP request is not valid UTF-8",
        )
    })
}

fn find_header_end(buffer: &[u8]) -> Option<usize> {
    if let Some(position) = buffer.windows(4).position(|window| window == b"\r\n\r\n") {
        return Some(position + 4);
    }

    buffer
        .windows(2)
        .position(|window| window == b"\n\n")
        .map(|position| position + 2)
}

fn parse_content_length(headers: &str) -> std::io::Result<usize> {
    for line in headers.lines().skip(1) {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };

        if name.trim().eq_ignore_ascii_case("Content-Length") {
            return value.trim().parse::<usize>().map_err(|_| {
                std::io::Error::new(std::io::ErrorKind::InvalidData, "invalid Content-Length")
            });
        }
    }

    Ok(0)
}

fn handle_connection(stream: &mut TcpStream, node: &mut SynoraNode) -> std::io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;

    let request = match read_http_request(stream) {
        Ok(request) => request,

        Err(error) => {
            let body = format!(
                r#"{{"status":"error","error":{{"code":"INVALID_HTTP_REQUEST","message":"{}"}}}}"#,
                json_escape(&error.to_string())
            );

            return send_response(stream, 400, &body);
        }
    };

    if request.trim().is_empty() {
        return send_response(
            stream,
            400,
            r#"{"status":"error","error":{"code":"EMPTY_REQUEST","message":"empty HTTP request"}}"#,
        );
    }

    let mut lines = request.lines();

    let request_line = match lines.next() {
        Some(line) => line.trim(),

        None => {
            return send_response(
                stream,
                400,
                r#"{"status":"error","error":{"code":"INVALID_REQUEST","message":"invalid HTTP request"}}"#,
            );
        }
    };

    let mut parts = request_line.split_whitespace();

    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("");
    let version = parts.next().unwrap_or("");

    println!("RPC            : {} {}", method, path);

    if version != "HTTP/1.1" && version != "HTTP/1.0" {
        return send_response(
            stream,
            400,
            r#"{"status":"error","error":{"code":"UNSUPPORTED_HTTP_VERSION","message":"unsupported HTTP version"}}"#,
        );
    }

    if method != "POST" && method != "GET" {
        return send_response(
            stream,
            405,
            r#"{"status":"error","error":{"code":"METHOD_NOT_ALLOWED","message":"method not allowed"}}"#,
        );
    }

    match (method, path) {
        ("GET", "/") => {
            let response = RootResponse {
                name: "Synora",
                version: synora_core::version(),
                chain_id: node.chain_id(),
            };

            send_json_response(
                stream,
                200,
                &RpcResponse {
                    status: "ok",
                    result: response,
                },
            )
        }

        ("GET", "/status") => {
            let latest_block_hash = encode_hex(&node.chain().latest_block().hash());

            let response = StatusResponse {
                chain_id: node.chain_id(),
                height: node.chain().height(),
                pending_transactions: node.pending_transactions(),
                latest_block_hash,
            };

            send_json_response(
                stream,
                200,
                &RpcResponse {
                    status: "ok",
                    result: response,
                },
            )
        }

        ("GET", "/state/root") => {
            let response = serde_json::json!({
                "root": encode_hex(&node.state().state_root()),
            });

            send_json_response(
                stream,
                200,
                &RpcResponse {
                    status: "ok",
                    result: response,
                },
            )
        }

        ("GET", path) if path.starts_with("/state/") => {
            let address_text = path.trim_start_matches("/state/");

            if address_text.is_empty() {
                return send_rpc_error(
                    stream,
                    400,
                    "INVALID_ADDRESS",
                    "account address is required",
                );
            }

            let address = match parse_address(address_text) {
                Ok(address) => address,

                Err(message) => {
                    return send_rpc_error(stream, 400, "INVALID_ADDRESS", &message);
                }
            };

            match node.state().get_account(&address) {
                Some(account) => {
                    let response = AccountResponse {
                        address: encode_hex(&address),
                        balance: account.balance.to_string(),
                        nonce: account.nonce,
                    };

                    send_json_response(
                        stream,
                        200,
                        &RpcResponse {
                            status: "ok",
                            result: response,
                        },
                    )
                }

                None => send_rpc_error(stream, 404, "ACCOUNT_NOT_FOUND", "account not found"),
            }
        }

        ("GET", "/mempool") => {
            /*
             * `transactions()` returns references to transactions.
             * Calling `.iter()` therefore produces &&Transaction.
             *
             * Use a closure so the compiler performs the required
             * dereference automatically.
             */
            let transactions = node
                .mempool()
                .transactions()
                .iter()
                .map(|transaction| transaction_to_json(transaction))
                .collect::<Vec<_>>();

            send_json_response(
                stream,
                200,
                &RpcResponse {
                    status: "ok",
                    result: transactions,
                },
            )
        }

        ("GET", "/block/latest") => {
            let block = node.chain().latest_block();

            send_json_response(
                stream,
                200,
                &RpcResponse {
                    status: "ok",
                    result: block_to_json(block),
                },
            )
        }

        ("GET", path) if path.starts_with("/block/") => {
            let height_text = path.trim_start_matches("/block/");

            if height_text.is_empty() {
                return send_rpc_error(
                    stream,
                    400,
                    "INVALID_BLOCK_HEIGHT",
                    "block height is required",
                );
            }

            let height = match height_text.parse::<u64>() {
                Ok(height) => height,

                Err(_) => {
                    return send_rpc_error(
                        stream,
                        400,
                        "INVALID_BLOCK_HEIGHT",
                        "block height must be a valid unsigned integer",
                    );
                }
            };

            match node.chain().block(height) {
                Some(block) => send_json_response(
                    stream,
                    200,
                    &RpcResponse {
                        status: "ok",
                        result: block_to_json(block),
                    },
                ),

                None => send_rpc_error(stream, 404, "BLOCK_NOT_FOUND", "block not found"),
            }
        }

        ("GET", path) if path.starts_with("/transaction/") => {
            let hash_text = path.trim_start_matches("/transaction/");

            let hash = match parse_hash(hash_text) {
                Ok(hash) => hash,

                Err(message) => {
                    return send_rpc_error(stream, 400, "INVALID_TRANSACTION_HASH", &message);
                }
            };

            match node.find_transaction(&hash) {
                Some((block_height, transaction)) => {
                    let status = if block_height.is_some() {
                        "confirmed"
                    } else {
                        "pending"
                    };

                    let response = TransactionResponse {
                        hash: encode_hex(&hash),
                        status,
                        block_height,
                        transaction: transaction_to_json(transaction),
                    };

                    send_json_response(
                        stream,
                        200,
                        &RpcResponse {
                            status: "ok",
                            result: response,
                        },
                    )
                }

                None => send_rpc_error(
                    stream,
                    404,
                    "TRANSACTION_NOT_FOUND",
                    "transaction not found",
                ),
            }
        }

        ("POST", "/transaction") => {
            let body = match extract_body(&request) {
                Some(body) => body,

                None => {
                    return send_rpc_error(stream, 400, "MISSING_BODY", "request body is required");
                }
            };

            let transaction_request = match serde_json::from_str::<TransactionRequest>(body) {
                Ok(request) => request,

                Err(error) => {
                    return send_rpc_error(
                        stream,
                        400,
                        "INVALID_TRANSACTION_JSON",
                        &error.to_string(),
                    );
                }
            };

            let transaction = match transaction_from_request(transaction_request) {
                Ok(transaction) => transaction,

                Err(message) => {
                    return send_rpc_error(stream, 400, "INVALID_TRANSACTION", &message);
                }
            };

            if let Err(error) = transaction.validate() {
                return send_rpc_error(
                    stream,
                    400,
                    "INVALID_TRANSACTION_INVARIANTS",
                    &format!("{error:?}"),
                );
            }

            if let Err(error) = transaction.verify_signature() {
                return send_rpc_error(stream, 400, "INVALID_SIGNATURE", &format!("{error:?}"));
            }

            let transaction_hash = transaction.hash();

            match node.submit_transaction(transaction.clone()) {
                Ok(()) => {
                    let response = TransactionResponse {
                        hash: encode_hex(&transaction_hash),
                        status: "pending",
                        block_height: None,
                        transaction: transaction_to_json(&transaction),
                    };

                    send_json_response(
                        stream,
                        200,
                        &RpcResponse {
                            status: "ok",
                            result: response,
                        },
                    )
                }

                Err(error) => {
                    let (code, status) = node_error_response(&error);

                    send_rpc_error(stream, status, code, &format!("{error:?}"))
                }
            }
        }

        ("POST", "/block/produce") => match node.produce_block(None) {
            Ok(block) => send_json_response(
                stream,
                200,
                &RpcResponse {
                    status: "ok",
                    result: block_to_json(&block),
                },
            ),

            Err(error) => {
                let (code, status) = node_error_response(&error);

                send_rpc_error(stream, status, code, &format!("{error:?}"))
            }
        },

        _ => send_rpc_error(stream, 404, "NOT_FOUND", "unknown RPC route"),
    }
}

fn node_error_response(error: &NodeError) -> (&'static str, u16) {
    match error {
        NodeError::Mempool(mempool_error) => match mempool_error {
            synora_core::mempool::MempoolError::DuplicateTransaction => {
                ("TRANSACTION_ALREADY_EXISTS", 409)
            }

            synora_core::mempool::MempoolError::MempoolFull => ("MEMPOOL_FULL", 400),

            synora_core::mempool::MempoolError::InvalidChainId => ("INVALID_CHAIN_ID", 400),

            synora_core::mempool::MempoolError::SenderNotFound => ("SENDER_NOT_FOUND", 400),

            synora_core::mempool::MempoolError::InvalidNonce => ("INVALID_NONCE", 400),

            synora_core::mempool::MempoolError::InsufficientBalance => {
                ("INSUFFICIENT_BALANCE", 400)
            }

            synora_core::mempool::MempoolError::InvalidTransaction => ("INVALID_TRANSACTION", 400),

            synora_core::mempool::MempoolError::SenderNonceConflict => {
                ("SENDER_NONCE_CONFLICT", 400)
            }

            synora_core::mempool::MempoolError::InvalidSignature => ("INVALID_SIGNATURE", 400),
        },

        NodeError::Chain(_) => ("CHAIN_ERROR", 400),

        NodeError::Consensus(_) => ("CONSENSUS_ERROR", 400),

        /*
         * A consensus commit requires the block itself.
         *
         * These are node-level validation errors rather than consensus
         * engine errors, so expose stable RPC error codes for them.
         */
        NodeError::ConsensusBlockRequired => ("CONSENSUS_BLOCK_REQUIRED", 400),

        NodeError::ConsensusBlockHashMismatch => ("CONSENSUS_BLOCK_HASH_MISMATCH", 400),

        NodeError::NoTransactions => ("NO_TRANSACTIONS", 400),

        NodeError::BlockGasLimitExceeded => ("BLOCK_GAS_LIMIT_EXCEEDED", 400),
    }
}

fn extract_body(request: &str) -> Option<&str> {
    if let Some(position) = request.find("\r\n\r\n") {
        return Some(&request[position + 4..]);
    }

    if let Some(position) = request.find("\n\n") {
        return Some(&request[position + 2..]);
    }

    None
}

fn transaction_from_request(request: TransactionRequest) -> Result<Transaction, String> {
    let sender = parse_address(&request.sender)?;
    let recipient = parse_address(&request.recipient)?;

    let data = decode_hex_field(&request.data, "data")?;

    let public_key = decode_fixed_hex::<PUBLIC_KEY_SIZE>(&request.public_key, "public_key")?;

    let signature = decode_fixed_hex::<SIGNATURE_SIZE>(&request.signature, "signature")?;

    Ok(Transaction {
        chain_id: request.chain_id,
        nonce: request.nonce,
        sender,
        recipient,
        value: request.value,
        gas_limit: request.gas_limit,
        gas_price: request.gas_price,
        data,
        public_key,
        signature,
    })
}

fn transaction_to_json(transaction: &Transaction) -> TransactionJson {
    TransactionJson {
        hash: encode_hex(&transaction.hash()),
        chain_id: transaction.chain_id,
        nonce: transaction.nonce,
        sender: encode_hex(&transaction.sender),
        recipient: encode_hex(&transaction.recipient),
        value: transaction.value,
        gas_limit: transaction.gas_limit,
        gas_price: transaction.gas_price,
        data: encode_hex(&transaction.data),
        public_key: encode_hex(&transaction.public_key),
        signature: encode_hex(&transaction.signature),
    }
}

fn block_to_json(block: &Block) -> BlockResponse {
    BlockResponse {
        hash: encode_hex(&block.hash()),
        version: block.header.version,
        chain_id: block.header.chain_id,
        height: block.header.height,
        timestamp: block.header.timestamp,
        previous_hash: encode_hex(&block.header.previous_hash),
        state_root: encode_hex(&block.header.state_root),
        transactions_root: encode_hex(&block.header.transactions_root),
        transaction_count: block.transactions.len(),

        /*
         * Here `.iter()` yields &Transaction directly, so the function
         * can be passed without an additional closure.
         */
        transactions: block.transactions.iter().map(transaction_to_json).collect(),
    }
}

fn parse_address(value: &str) -> Result<Address, String> {
    decode_fixed_hex::<20>(value, "address")
}

fn parse_hash(value: &str) -> Result<Hash, String> {
    decode_fixed_hex::<32>(value, "transaction hash")
}

fn decode_fixed_hex<const N: usize>(value: &str, field: &str) -> Result<[u8; N], String> {
    let value = value.trim();

    if value.len() != N * 2 {
        return Err(format!(
            "{field} must contain exactly {} hexadecimal characters",
            N * 2
        ));
    }

    let bytes = decode_hex_field(value, field)?;

    bytes
        .try_into()
        .map_err(|_| format!("invalid {field} length"))
}

fn decode_hex_field(value: &str, field: &str) -> Result<Vec<u8>, String> {
    let value = value.trim();

    if value.is_empty() {
        return Ok(Vec::new());
    }

    if !value.len().is_multiple_of(2) {
        return Err(format!(
            "{field} must contain an even number of hexadecimal characters"
        ));
    }

    let bytes = value.as_bytes();

    let mut output = Vec::with_capacity(bytes.len() / 2);

    for index in (0..bytes.len()).step_by(2) {
        let high = hex_value(bytes[index])
            .ok_or_else(|| format!("invalid hexadecimal character in {field}"))?;

        let low = hex_value(bytes[index + 1])
            .ok_or_else(|| format!("invalid hexadecimal character in {field}"))?;

        output.push((high << 4) | low);
    }

    Ok(output)
}

fn hex_value(value: u8) -> Option<u8> {
    match value {
        b'0'..=b'9' => Some(value - b'0'),
        b'a'..=b'f' => Some(value - b'a' + 10),
        b'A'..=b'F' => Some(value - b'A' + 10),
        _ => None,
    }
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";

    let mut output = String::with_capacity(bytes.len() * 2);

    for &byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }

    output
}

fn send_json_response<T: Serialize>(
    stream: &mut TcpStream,
    status_code: u16,
    response: &T,
) -> std::io::Result<()> {
    let body = serde_json::to_string(response)
        .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string()))?;

    send_response(stream, status_code, &body)
}

fn send_rpc_error(
    stream: &mut TcpStream,
    status_code: u16,
    code: &'static str,
    message: &str,
) -> std::io::Result<()> {
    let response = RpcErrorResponse {
        status: "error",
        error: RpcError {
            code,
            message: message.to_string(),
        },
    };

    send_json_response(stream, status_code, &response)
}

fn send_response(stream: &mut TcpStream, status_code: u16, body: &str) -> std::io::Result<()> {
    let reason = match status_code {
        200 => "OK",
        400 => "Bad Request",
        404 => "Not Found",
        405 => "Method Not Allowed",
        409 => "Conflict",
        _ => "Internal Server Error",
    };

    let response = format!(
        "HTTP/1.1 {} {}\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n\
         {}",
        status_code,
        reason,
        body.len(),
        body
    );

    stream.write_all(response.as_bytes())?;
    stream.flush()
}

fn json_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::node::SynoraNode;
    use synora_core::{block::Block, crypto::Keypair};

    fn test_transaction() -> Transaction {
        let keypair = Keypair::from_bytes(&[1u8; 32]);

        let mut tx = Transaction::new(
            1337,
            0,
            keypair.address(),
            [2u8; 20],
            10_000,
            21_000,
            1,
            vec![1, 2, 3],
        );

        tx.sign(&keypair).expect("transaction should be signable");

        tx
    }

    #[test]
    fn address_roundtrip_works() {
        let address = [0xabu8; 20];

        let encoded = encode_hex(&address);
        let decoded = parse_address(&encoded).expect("address should decode");

        assert_eq!(decoded, address);
    }

    #[test]
    fn invalid_address_is_rejected() {
        assert!(parse_address("1234").is_err());
        assert!(parse_address("zz".repeat(20).as_str()).is_err());
    }

    #[test]
    fn fixed_hex_requires_exact_length() {
        assert!(decode_fixed_hex::<32>("aa", "public_key").is_err());

        assert!(decode_fixed_hex::<32>(&"aa".repeat(32), "public_key").is_ok());
    }

    #[test]
    fn transaction_roundtrip_preserves_signature() {
        let tx = test_transaction();

        let request = TransactionRequest {
            chain_id: tx.chain_id,
            nonce: tx.nonce,
            sender: encode_hex(&tx.sender),
            recipient: encode_hex(&tx.recipient),
            value: tx.value,
            gas_limit: tx.gas_limit,
            gas_price: tx.gas_price,
            data: encode_hex(&tx.data),
            public_key: encode_hex(&tx.public_key),
            signature: encode_hex(&tx.signature),
        };

        let restored = transaction_from_request(request).expect("transaction should decode");

        assert_eq!(restored, tx);

        restored
            .verify_signature()
            .expect("restored signature should remain valid");
    }

    #[test]
    fn transaction_json_contains_expected_fields() {
        let tx = test_transaction();

        let json =
            serde_json::to_string(&transaction_to_json(&tx)).expect("transaction should serialize");

        assert!(json.contains("\"hash\":"));
        assert!(json.contains("\"chain_id\":1337"));
        assert!(json.contains("\"nonce\":0"));
        assert!(json.contains("\"sender\":"));
        assert!(json.contains("\"recipient\":"));
        assert!(json.contains("\"data\":\"010203\""));
        assert!(json.contains("\"public_key\":"));
        assert!(json.contains("\"signature\":"));
    }

    #[test]
    fn block_json_contains_expected_fields() {
        let block = Block::genesis(1337, 1_700_000_000);

        let json = serde_json::to_string(&block_to_json(&block)).expect("block should serialize");

        assert!(json.contains("\"hash\":"));
        assert!(json.contains("\"version\":1"));
        assert!(json.contains("\"chain_id\":1337"));
        assert!(json.contains("\"height\":0"));
        assert!(json.contains("\"timestamp\":1700000000"));
        assert!(json.contains("\"previous_hash\":"));
        assert!(json.contains("\"state_root\":"));
        assert!(json.contains("\"transactions_root\":"));
        assert!(json.contains("\"transaction_count\":0"));
        assert!(json.contains("\"transactions\":[]"));
    }

    #[test]
    fn invalid_block_height_is_rejected() {
        assert!("abc".parse::<u64>().is_err());
        assert!("".parse::<u64>().is_err());
    }

    #[test]
    fn invalid_signature_is_rejected() {
        let keypair = Keypair::from_bytes(&[1u8; 32]);

        let mut tx = Transaction::new(
            1337,
            0,
            keypair.address(),
            [2u8; 20],
            10_000,
            21_000,
            1,
            Vec::new(),
        );

        tx.sign(&keypair).expect("transaction should be signable");

        tx.signature[0] ^= 0xff;

        assert!(tx.verify_signature().is_err());
    }

    #[test]
    fn root_response_contains_metadata() {
        let config = crate::config::NodeConfig::new(1337, [0xfe; 20], 100, 1_000_000);

        let node = SynoraNode::new(config, 1_700_000_000);

        assert_eq!(node.chain_id(), 1337);
        assert_eq!(synora_core::version(), "0.1.0");
    }

    #[test]
    fn rpc_server_address_is_exposed() {
        let rpc = RpcServer::new("127.0.0.1:8545");

        assert_eq!(rpc.address(), "127.0.0.1:8545");
    }

    #[test]
    fn hash_encoding_is_lowercase_hex() {
        let hash = [0xab; 32];

        assert_eq!(
            encode_hex(&hash),
            "abababababababababababababababababababababababababababababababab"
        );
    }
}
