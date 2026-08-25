use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    time::{SystemTime, UNIX_EPOCH},
};

use synora_core::{block::Block, state::Address, transaction::Transaction};

use crate::node::{NodeError, SynoraNode};

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

        println!();
        println!("=================================");
        println!("         SYNORA RPC SERVER       ");
        println!("=================================");
        println!("RPC Address    : http://{}", self.address);
        println!("Status         : LISTENING");
        println!();

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
}

fn handle_connection(stream: &mut TcpStream, node: &mut SynoraNode) -> std::io::Result<()> {
    let mut buffer = [0u8; 16 * 1024];

    let size = stream.read(&mut buffer)?;

    if size == 0 {
        return Ok(());
    }

    let request = String::from_utf8_lossy(&buffer[..size]);

    let mut lines = request.lines();

    let request_line = match lines.next() {
        Some(line) => line,

        None => {
            return send_response(
                stream,
                400,
                "application/json",
                r#"{"error":"invalid HTTP request"}"#,
            );
        }
    };

    let mut parts = request_line.split_whitespace();

    let method = parts.next().unwrap_or("");
    let path = parts.next().unwrap_or("/");

    println!("RPC            : {} {}", method, path);

    /*
     * POST /transaction
     *
     * Transaction body is extracted from the HTTP request.
     */
    if method == "POST" && path == "/transaction" {
        let body = match extract_body(&request) {
            Some(body) => body,

            None => {
                return send_response(
                    stream,
                    400,
                    "application/json",
                    r#"{"error":"request body is required"}"#,
                );
            }
        };

        return handle_submit_transaction(stream, node, body);
    }

    let response = match (method, path) {
        ("GET", "/") => (
            200,
            r#"{
  "name":"Synora",
  "version":"0.1.0",
  "service":"rpc"
}"#,
        ),

        ("GET", "/status") => {
            let json = status_json(node);

            return send_response(stream, 200, "application/json", &json);
        }

        ("GET", "/block/latest") => {
            let json = block_json(node.chain().latest_block());

            return send_response(stream, 200, "application/json", &json);
        }

        ("GET", "/mempool") => {
            let json = format!(
                r#"{{"pending_transactions":{}}}"#,
                node.pending_transactions()
            );

            return send_response(stream, 200, "application/json", &json);
        }

        ("POST", "/block/produce") => match node.produce_block(None) {
            Ok(block) => {
                let json = block_json(&block);

                return send_response(stream, 200, "application/json", &json);
            }

            Err(NodeError::NoTransactions) => {
                return send_response(
                    stream,
                    409,
                    "application/json",
                    r#"{"error":"no transactions available"}"#,
                );
            }

            Err(NodeError::BlockGasLimitExceeded) => {
                return send_response(
                    stream,
                    409,
                    "application/json",
                    r#"{"error":"block gas limit exceeded"}"#,
                );
            }

            Err(error) => {
                let json = format!(r#"{{"error":"{}"}}"#, json_escape(&format!("{:?}", error)));

                return send_response(stream, 500, "application/json", &json);
            }
        },

        _ => {
            if method == "GET" {
                if let Some(height) = path.strip_prefix("/block/") {
                    match height.parse::<u64>() {
                        Ok(height) => match node.chain().block(height) {
                            Some(block) => {
                                let json = block_json(block);

                                return send_response(stream, 200, "application/json", &json);
                            }

                            None => {
                                return send_response(
                                    stream,
                                    404,
                                    "application/json",
                                    r#"{"error":"block not found"}"#,
                                );
                            }
                        },

                        Err(_) => {
                            return send_response(
                                stream,
                                400,
                                "application/json",
                                r#"{"error":"invalid block height"}"#,
                            );
                        }
                    }
                }

                if let Some(address) = path.strip_prefix("/state/") {
                    match parse_address(address) {
                        Some(address) => {
                            let account = node.state().get_account(&address);

                            match account {
                                Some(account) => {
                                    let json = format!(
                                        r#"{{"address":"{}","balance":{},"nonce":{}}}"#,
                                        address_hex(&address),
                                        account.balance,
                                        account.nonce
                                    );

                                    return send_response(stream, 200, "application/json", &json);
                                }

                                None => {
                                    return send_response(
                                        stream,
                                        404,
                                        "application/json",
                                        r#"{"error":"account not found"}"#,
                                    );
                                }
                            }
                        }

                        None => {
                            return send_response(
                                stream,
                                400,
                                "application/json",
                                r#"{"error":"invalid address"}"#,
                            );
                        }
                    }
                }
            }

            return send_response(
                stream,
                404,
                "application/json",
                r#"{"error":"route not found"}"#,
            );
        }
    };

    send_response(stream, response.0, "application/json", response.1)
}

fn handle_submit_transaction(
    stream: &mut TcpStream,
    node: &mut SynoraNode,
    body: &str,
) -> std::io::Result<()> {
    let transaction = match parse_transaction_json(body) {
        Ok(transaction) => transaction,

        Err(error) => {
            let json = format!(r#"{{"error":"{}"}}"#, json_escape(&error));

            return send_response(stream, 400, "application/json", &json);
        }
    };

    let transaction_hash = transaction.hash();

    match node.submit_transaction(transaction) {
        Ok(()) => {
            let json = format!(
                r#"{{
  "status":"accepted",
  "hash":"{}",
  "pending_transactions":{}
}}"#,
                hash_hex(&transaction_hash),
                node.pending_transactions(),
            );

            send_response(stream, 200, "application/json", &json)
        }

        Err(error) => {
            let status = match error {
                NodeError::Mempool(_) => 409,
                NodeError::Chain(_) => 400,
                NodeError::NoTransactions => 409,
                NodeError::BlockGasLimitExceeded => 409,
            };

            let json = format!(
                r#"{{"status":"rejected","error":"{}"}}"#,
                json_escape(&format!("{:?}", error))
            );

            send_response(stream, status, "application/json", &json)
        }
    }
}

fn parse_transaction_json(body: &str) -> Result<Transaction, String> {
    let chain_id =
        json_u64(body, "chain_id").ok_or_else(|| "missing or invalid chain_id".to_string())?;

    let nonce = json_u64(body, "nonce").ok_or_else(|| "missing or invalid nonce".to_string())?;

    let sender_text = json_string(body, "sender").ok_or_else(|| "missing sender".to_string())?;

    let recipient_text =
        json_string(body, "recipient").ok_or_else(|| "missing recipient".to_string())?;

    let sender = parse_address(&sender_text).ok_or_else(|| "invalid sender address".to_string())?;

    let recipient =
        parse_address(&recipient_text).ok_or_else(|| "invalid recipient address".to_string())?;

    let value = json_u64(body, "value").ok_or_else(|| "missing or invalid value".to_string())?;

    let gas_limit =
        json_u64(body, "gas_limit").ok_or_else(|| "missing or invalid gas_limit".to_string())?;

    let gas_price =
        json_u64(body, "gas_price").ok_or_else(|| "missing or invalid gas_price".to_string())?;

    let data_text = json_string(body, "data").unwrap_or_default();

    let data = parse_hex_bytes(&data_text).ok_or_else(|| "invalid data hex".to_string())?;

    Ok(Transaction::new(
        chain_id, nonce, sender, recipient, value, gas_limit, gas_price, data,
    ))
}

fn extract_body(request: &str) -> Option<&str> {
    request
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .or_else(|| request.split_once("\n\n").map(|(_, body)| body))
}

fn json_u64(body: &str, key: &str) -> Option<u64> {
    let marker = format!(r#""{}":"#, key);

    let start = body.find(&marker)?;
    let rest = &body[start + marker.len()..];

    let rest = rest.trim_start();

    let end = rest
        .find(|character: char| !character.is_ascii_digit())
        .unwrap_or(rest.len());

    if end == 0 {
        return None;
    }

    rest[..end].parse().ok()
}

fn json_string(body: &str, key: &str) -> Option<String> {
    let marker = format!(r#""{}":"#, key);

    let start = body.find(&marker)?;
    let rest = &body[start + marker.len()..];

    let rest = rest.trim_start();

    if !rest.starts_with('"') {
        return None;
    }

    let rest = &rest[1..];

    let end = rest.find('"')?;

    Some(rest[..end].to_string())
}

fn parse_hex_bytes(value: &str) -> Option<Vec<u8>> {
    let value = value.strip_prefix("0x").unwrap_or(value);

    if value.is_empty() {
        return Some(Vec::new());
    }

    if !value.len().is_multiple_of(2) {
        return None;
    }

    let mut result = Vec::with_capacity(value.len() / 2);

    for chunk in value.as_bytes().chunks(2) {
        let text = std::str::from_utf8(chunk).ok()?;

        result.push(u8::from_str_radix(text, 16).ok()?);
    }

    Some(result)
}

fn status_json(node: &SynoraNode) -> String {
    format!(
        r#"{{
  "name":"Synora",
  "version":"0.1.0",
  "chain_id":{},
  "height":{},
  "block_count":{},
  "mempool":{},
  "latest_block_hash":"{}",
  "status":"ready"
}}"#,
        node.chain().chain_id(),
        node.chain().height(),
        node.chain().block_count(),
        node.pending_transactions(),
        hash_hex(&node.chain().latest_block().hash()),
    )
}

fn block_json(block: &Block) -> String {
    let transactions = block
        .transactions
        .iter()
        .map(|tx| {
            format!(
                r#"{{
      "hash":"{}",
      "chain_id":{},
      "nonce":{},
      "sender":"{}",
      "recipient":"{}",
      "value":{},
      "gas_limit":{},
      "gas_price":{}
    }}"#,
                hash_hex(&tx.hash()),
                tx.chain_id,
                tx.nonce,
                address_hex(&tx.sender),
                address_hex(&tx.recipient),
                tx.value,
                tx.gas_limit,
                tx.gas_price,
            )
        })
        .collect::<Vec<_>>()
        .join(",");

    format!(
        r#"{{
  "header":{{
    "version":{},
    "chain_id":{},
    "height":{},
    "timestamp":{},
    "previous_hash":"{}",
    "state_root":"{}",
    "transactions_root":"{}"
  }},
  "transaction_count":{},
  "transactions":[{}]
}}"#,
        block.header.version,
        block.header.chain_id,
        block.header.height,
        block.header.timestamp,
        hash_hex(&block.header.previous_hash),
        hash_hex(&block.header.state_root),
        hash_hex(&block.header.transactions_root),
        block.transactions.len(),
        transactions,
    )
}

fn send_response(
    stream: &mut TcpStream,
    status: u16,
    content_type: &str,
    body: &str,
) -> std::io::Result<()> {
    let status_text = match status {
        200 => "OK",
        400 => "Bad Request",
        404 => "Not Found",
        409 => "Conflict",
        500 => "Internal Server Error",
        _ => "Unknown",
    };

    let response = format!(
        "HTTP/1.1 {} {}\r\n\
         Content-Type: {}\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n\
         {}",
        status,
        status_text,
        content_type,
        body.len(),
        body,
    );

    stream.write_all(response.as_bytes())?;
    stream.flush()?;

    Ok(())
}

fn address_hex(address: &Address) -> String {
    let mut result = String::with_capacity(42);

    result.push_str("0x");

    for byte in address {
        result.push_str(&format!("{:02x}", byte));
    }

    result
}

fn hash_hex(hash: &[u8; 32]) -> String {
    let mut result = String::with_capacity(66);

    result.push_str("0x");

    for byte in hash {
        result.push_str(&format!("{:02x}", byte));
    }

    result
}

fn parse_address(value: &str) -> Option<Address> {
    let value = value.strip_prefix("0x").unwrap_or(value);

    if value.len() != 40 {
        return None;
    }

    let mut address = [0u8; 20];

    for (index, chunk) in value.as_bytes().chunks(2).enumerate() {
        let text = std::str::from_utf8(chunk).ok()?;

        address[index] = u8::from_str_radix(text, 16).ok()?;
    }

    Some(address)
}

fn json_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
        .replace('\t', "\\t")
}

#[allow(dead_code)]
fn current_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
