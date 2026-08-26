use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
    time::Duration,
};

use synora_core::{crypto::Keypair, transaction::Transaction};

use synora_node::{config::NodeConfig, node::SynoraNode, rpc::RpcServer};

fn free_local_address() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind temporary port");

    listener
        .local_addr()
        .expect("get temporary address")
        .to_string()
}

fn start_rpc_server() -> String {
    let address = free_local_address();

    let config = NodeConfig::new(1337, [3u8; 20], 100, 1_000_000);

    let mut node = SynoraNode::new(config, 1_700_000_000);

    let sender_keypair = Keypair::from_bytes(&[1u8; 32]);
    let sender_address = sender_keypair.address();

    node.create_account(sender_address, 1_000_000);
    node.create_account([2u8; 20], 0);
    node.create_account([3u8; 20], 0);

    let rpc = RpcServer::new(address.clone());

    thread::spawn(move || {
        rpc.run(&mut node)
            .expect("RPC server should run successfully");
    });

    wait_for_server(&address);

    address
}

fn wait_for_server(address: &str) {
    for _ in 0..50 {
        match TcpStream::connect(address) {
            Ok(_) => return,
            Err(_) => {
                thread::sleep(Duration::from_millis(20));
            }
        }
    }

    panic!("RPC server did not start at {address}");
}

fn http_get(address: &str, path: &str) -> String {
    let mut stream = TcpStream::connect(address).expect("connect to RPC server");

    let request = format!(
        "GET {} HTTP/1.1\r\n\
         Host: {}\r\n\
         Connection: close\r\n\
         \r\n",
        path, address
    );

    stream
        .write_all(request.as_bytes())
        .expect("write HTTP request");

    let mut response = String::new();

    stream
        .read_to_string(&mut response)
        .expect("read HTTP response");

    response
}

fn http_post(address: &str, path: &str, body: &str) -> String {
    let mut stream = TcpStream::connect(address).expect("connect to RPC server");

    let request = format!(
        "POST {} HTTP/1.1\r\n\
         Host: {}\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n\
         {}",
        path,
        address,
        body.len(),
        body
    );

    stream
        .write_all(request.as_bytes())
        .expect("write HTTP request");

    let mut response = String::new();

    stream
        .read_to_string(&mut response)
        .expect("read HTTP response");

    response
}

fn assert_http_status(response: &str, status: &str) {
    let actual_code = response
        .lines()
        .next()
        .unwrap_or("")
        .split_whitespace()
        .nth(1)
        .unwrap_or("");

    let expected_code = status.split_whitespace().nth(1).unwrap_or("");

    assert_eq!(
        actual_code, expected_code,
        "expected HTTP status {status}, got:\n{response}"
    );
}

fn assert_json_contains(response: &str, value: &str) {
    assert!(
        response.contains(value),
        "expected response to contain {value:?}, got:\n{response}"
    );
}

fn sender_address_hex() -> String {
    let keypair = Keypair::from_bytes(&[1u8; 32]);

    keypair
        .address()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn signed_transaction() -> Transaction {
    let keypair = Keypair::from_bytes(&[1u8; 32]);

    let mut transaction = Transaction::new(
        1337,
        0,
        keypair.address(),
        [2u8; 20],
        1_000,
        21_000,
        1,
        Vec::new(),
    );

    transaction
        .sign(&keypair)
        .expect("transaction should be signable");

    transaction
}

fn transaction_json(transaction: &Transaction) -> String {
    serde_json::json!({
        "chain_id": transaction.chain_id,
        "nonce": transaction.nonce,
        "sender": hex_encode(&transaction.sender),
        "recipient": hex_encode(&transaction.recipient),
        "value": transaction.value,
        "gas_limit": transaction.gas_limit,
        "gas_price": transaction.gas_price,
        "data": hex_encode(&transaction.data),
        "public_key": hex_encode(&transaction.public_key),
        "signature": hex_encode(&transaction.signature),
    })
    .to_string()
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";

    let mut output = String::with_capacity(bytes.len() * 2);

    for &byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }

    output
}

#[test]
fn rpc_root_endpoint_works_against_real_server() {
    let address = start_rpc_server();

    let response = http_get(&address, "/");

    assert_http_status(&response, "HTTP/1.1 200 OK");
    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, "\"name\":\"Synora\"");
    assert_json_contains(&response, "\"chain_id\":1337");
}

#[test]
fn rpc_status_endpoint_works_against_real_server() {
    let address = start_rpc_server();

    let response = http_get(&address, "/status");

    assert_http_status(&response, "HTTP/1.1 200 OK");
    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, "\"chain_id\":1337");
    assert_json_contains(&response, "\"height\":0");
    assert_json_contains(&response, "\"pending_transactions\":0");
    assert_json_contains(&response, "\"latest_block_hash\":");
}

#[test]
fn rpc_state_root_endpoint_works_against_real_server() {
    let address = start_rpc_server();

    let response = http_get(&address, "/state/root");

    assert_http_status(&response, "HTTP/1.1 200 OK");
    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, "\"root\":");
}

#[test]
fn rpc_mempool_endpoint_works_against_real_server() {
    let address = start_rpc_server();

    let response = http_get(&address, "/mempool");

    assert_http_status(&response, "HTTP/1.1 200 OK");
    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, "\"result\":[]");
}

#[test]
fn rpc_unknown_route_returns_not_found() {
    let address = start_rpc_server();

    let response = http_get(&address, "/does-not-exist");

    assert_http_status(&response, "HTTP/1.1 404 Not Found");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"NOT_FOUND\"");
}

#[test]
fn rpc_method_not_allowed_returns_error() {
    let address = start_rpc_server();

    let mut stream = TcpStream::connect(&address).expect("connect to RPC server");

    let request = format!(
        "PUT /status HTTP/1.1\r\n\
         Host: {}\r\n\
         Connection: close\r\n\
         \r\n",
        address
    );

    stream
        .write_all(request.as_bytes())
        .expect("write HTTP request");

    let mut response = String::new();

    stream
        .read_to_string(&mut response)
        .expect("read HTTP response");

    assert_http_status(&response, "HTTP/1.1 405 Method Not Allowed");
    assert_json_contains(&response, "\"code\":\"METHOD_NOT_ALLOWED\"");
}

#[test]
fn rpc_invalid_transaction_json_returns_error() {
    let address = start_rpc_server();

    let response = http_post(&address, "/transaction", "{invalid-json");

    assert_http_status(&response, "HTTP/1.1 400 Bad Request");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"INVALID_TRANSACTION_JSON\"");
}

#[test]
fn rpc_missing_transaction_body_returns_error() {
    let address = start_rpc_server();

    let response = http_post(&address, "/transaction", "");

    assert_http_status(&response, "HTTP/1.1 400 Bad Request");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"INVALID_TRANSACTION_JSON\"");
}

#[test]
fn rpc_invalid_transaction_hash_returns_error() {
    let address = start_rpc_server();

    let response = http_get(&address, "/transaction/not-a-valid-hash");

    assert_http_status(&response, "HTTP/1.1 400 Bad Request");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"INVALID_TRANSACTION_HASH\"");
}

#[test]
fn rpc_unknown_transaction_returns_not_found() {
    let address = start_rpc_server();

    let hash = "0000000000000000000000000000000000000000000000000000000000000000";

    let response = http_get(&address, &format!("/transaction/{hash}"));

    assert_http_status(&response, "HTTP/1.1 404 Not Found");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"TRANSACTION_NOT_FOUND\"");
}

#[test]
fn rpc_submit_signed_transaction_against_real_server() {
    let address = start_rpc_server();

    let transaction = signed_transaction();
    let expected_hash = hex_encode(&transaction.hash());

    let body = transaction_json(&transaction);

    let response = http_post(&address, "/transaction", &body);

    assert_http_status(&response, "HTTP/1.1 200 OK");
    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, "\"status\":\"pending\"");
    assert_json_contains(&response, &format!("\"hash\":\"{expected_hash}\""));
    assert_json_contains(&response, "\"chain_id\":1337");
    assert_json_contains(&response, "\"nonce\":0");
}

#[test]
fn rpc_submit_transaction_then_query_transaction() {
    let address = start_rpc_server();

    let transaction = signed_transaction();
    let expected_hash = hex_encode(&transaction.hash());

    let body = transaction_json(&transaction);

    let submit_response = http_post(&address, "/transaction", &body);

    assert_http_status(&submit_response, "HTTP/1.1 200 OK");
    assert_json_contains(&submit_response, &format!("\"hash\":\"{expected_hash}\""));

    let query_response = http_get(&address, &format!("/transaction/{expected_hash}"));

    assert_http_status(&query_response, "HTTP/1.1 200 OK");
    assert_json_contains(&query_response, "\"status\":\"ok\"");
    assert_json_contains(&query_response, "\"status\":\"pending\"");
    assert_json_contains(&query_response, &format!("\"hash\":\"{expected_hash}\""));
}

#[test]
fn rpc_submit_transaction_then_mempool_contains_it() {
    let address = start_rpc_server();

    let transaction = signed_transaction();
    let expected_hash = hex_encode(&transaction.hash());

    let body = transaction_json(&transaction);

    let submit_response = http_post(&address, "/transaction", &body);

    assert_http_status(&submit_response, "HTTP/1.1 200 OK");

    let mempool_response = http_get(&address, "/mempool");

    assert_http_status(&mempool_response, "HTTP/1.1 200 OK");
    assert_json_contains(&mempool_response, "\"status\":\"ok\"");
    assert_json_contains(&mempool_response, &format!("\"chain_id\":1337"));
    assert_json_contains(
        &mempool_response,
        &format!("\"sender\":\"{}\"", hex_encode(&transaction.sender)),
    );
    assert_json_contains(
        &mempool_response,
        &format!("\"recipient\":\"{}\"", hex_encode(&transaction.recipient)),
    );
    assert_json_contains(
        &mempool_response,
        &format!("\"signature\":\"{}\"", hex_encode(&transaction.signature)),
    );

    assert_json_contains(&mempool_response, &expected_hash);
}

#[test]
fn rpc_duplicate_transaction_returns_conflict() {
    let address = start_rpc_server();

    let transaction = signed_transaction();
    let body = transaction_json(&transaction);

    let first_response = http_post(&address, "/transaction", &body);

    assert_http_status(&first_response, "HTTP/1.1 200 OK");

    let second_response = http_post(&address, "/transaction", &body);

    assert_http_status(&second_response, "HTTP/1.1 409 Conflict");
    assert_json_contains(&second_response, "\"status\":\"error\"");
    assert_json_contains(&second_response, "\"code\":\"TRANSACTION_ALREADY_EXISTS\"");
}

#[test]
fn rpc_invalid_signature_returns_error() {
    let address = start_rpc_server();

    let mut transaction = signed_transaction();

    transaction.signature[0] ^= 0xff;

    let body = transaction_json(&transaction);

    let response = http_post(&address, "/transaction", &body);

    assert_http_status(&response, "HTTP/1.1 400 Bad Request");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"INVALID_SIGNATURE\"");
}

#[test]
fn rpc_wrong_chain_id_returns_error() {
    let address = start_rpc_server();

    let keypair = Keypair::from_bytes(&[1u8; 32]);

    let mut transaction = Transaction::new(
        9999,
        0,
        keypair.address(),
        [2u8; 20],
        1_000,
        21_000,
        1,
        Vec::new(),
    );

    transaction
        .sign(&keypair)
        .expect("transaction should be signable");

    let body = transaction_json(&transaction);

    let response = http_post(&address, "/transaction", &body);

    assert_http_status(&response, "HTTP/1.1 400 Bad Request");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"INVALID_CHAIN_ID\"");
}

#[test]
fn rpc_wrong_nonce_returns_error() {
    let address = start_rpc_server();

    let keypair = Keypair::from_bytes(&[1u8; 32]);

    let mut transaction = Transaction::new(
        1337,
        99,
        keypair.address(),
        [2u8; 20],
        1_000,
        21_000,
        1,
        Vec::new(),
    );

    transaction
        .sign(&keypair)
        .expect("transaction should be signable");

    let body = transaction_json(&transaction);

    let response = http_post(&address, "/transaction", &body);

    assert_http_status(&response, "HTTP/1.1 400 Bad Request");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"INVALID_NONCE\"");
}

#[test]
fn rpc_latest_block_endpoint_works_against_real_server() {
    let address = start_rpc_server();

    let response = http_get(&address, "/block/latest");

    assert_http_status(&response, "HTTP/1.1 200 OK");
    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, "\"hash\":");
    assert_json_contains(&response, "\"version\":1");
    assert_json_contains(&response, "\"chain_id\":1337");
    assert_json_contains(&response, "\"height\":0");
    assert_json_contains(&response, "\"transaction_count\":0");
    assert_json_contains(&response, "\"transactions\":[]");
}

#[test]
fn rpc_block_by_height_endpoint_works_against_real_server() {
    let address = start_rpc_server();

    let response = http_get(&address, "/block/0");

    assert_http_status(&response, "HTTP/1.1 200 OK");
    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, "\"hash\":");
    assert_json_contains(&response, "\"chain_id\":1337");
    assert_json_contains(&response, "\"height\":0");
    assert_json_contains(&response, "\"timestamp\":1700000000");
    assert_json_contains(&response, "\"transaction_count\":0");
}

#[test]
fn rpc_unknown_block_height_returns_not_found() {
    let address = start_rpc_server();

    let response = http_get(&address, "/block/999");

    assert_http_status(&response, "HTTP/1.1 404 Not Found");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"BLOCK_NOT_FOUND\"");
}

#[test]
fn rpc_invalid_block_height_returns_error() {
    let address = start_rpc_server();

    let response = http_get(&address, "/block/abc");

    assert_http_status(&response, "HTTP/1.1 400 Bad Request");
    assert_json_contains(&response, "\"status\":\"error\"");
    assert_json_contains(&response, "\"code\":\"INVALID_BLOCK_HEIGHT\"");
}

#[test]
fn rpc_block_produce_endpoint_works_against_real_server() {
    let address = start_rpc_server();

    let transaction = signed_transaction();
    let body = transaction_json(&transaction);

    let submit_response = http_post(&address, "/transaction", &body);

    assert_http_status(&submit_response, "HTTP/1.1 200 OK");
    assert_json_contains(&submit_response, "\"status\":\"ok\"");

    let mempool_response = http_get(&address, "/mempool");

    assert_http_status(&mempool_response, "HTTP/1.1 200 OK");
    assert_json_contains(&mempool_response, "\"status\":\"ok\"");
    assert_json_contains(&mempool_response, "\"result\":[");
    assert_json_contains(&mempool_response, "\"hash\":");

    let response = http_post(&address, "/block/produce", "");

    assert_http_status(&response, "HTTP/1.1 200 OK");
    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, "\"hash\":");
    assert_json_contains(&response, "\"version\":1");
    assert_json_contains(&response, "\"chain_id\":1337");
    assert_json_contains(&response, "\"height\":1");
    assert_json_contains(&response, "\"transaction_count\":1");
    assert_json_contains(&response, "\"transactions\":[");
    assert_json_contains(&response, "\"nonce\":0");

    let latest = http_get(&address, "/block/latest");

    assert_http_status(&latest, "HTTP/1.1 200 OK");
    assert_json_contains(&latest, "\"height\":1");
    assert_json_contains(&latest, "\"transaction_count\":1");
    assert_json_contains(&latest, "\"transactions\":[");
}

#[test]
fn rpc_produced_block_links_to_genesis() {
    let address = start_rpc_server();

    let genesis = http_get(&address, "/block/0");

    assert_http_status(&genesis, "HTTP/1.1 200 OK");
    assert_json_contains(&genesis, "\"height\":0");

    let genesis_json: serde_json::Value =
        serde_json::from_str(genesis.split("\r\n\r\n").nth(1).unwrap())
            .expect("genesis response should be valid JSON");

    let genesis_hash = genesis_json["result"]["hash"]
        .as_str()
        .expect("genesis hash should be present")
        .to_string();

    let transaction = signed_transaction();
    let body = transaction_json(&transaction);

    let submit = http_post(&address, "/transaction", &body);

    assert_http_status(&submit, "HTTP/1.1 200 OK");

    let produced = http_post(&address, "/block/produce", "");

    assert_http_status(&produced, "HTTP/1.1 200 OK");
    assert_json_contains(&produced, "\"height\":1");

    let produced_json: serde_json::Value =
        serde_json::from_str(produced.split("\r\n\r\n").nth(1).unwrap())
            .expect("produced response should be valid JSON");

    let previous_hash = produced_json["result"]["previous_hash"]
        .as_str()
        .expect("previous_hash should be present");

    assert_eq!(
        previous_hash, genesis_hash,
        "block #1 must reference genesis block hash"
    );
}

#[test]
fn rpc_second_produced_block_links_to_first_block() {
    let address = start_rpc_server();

    let first_transaction = signed_transaction();
    let first_body = transaction_json(&first_transaction);

    let first_submit = http_post(&address, "/transaction", &first_body);

    assert_http_status(&first_submit, "HTTP/1.1 200 OK");

    let first_block = http_post(&address, "/block/produce", "");

    assert_http_status(&first_block, "HTTP/1.1 200 OK");
    assert_json_contains(&first_block, "\"height\":1");

    let first_block_json: serde_json::Value =
        serde_json::from_str(first_block.split("\r\n\r\n").nth(1).unwrap())
            .expect("first block response should be valid JSON");

    let first_hash = first_block_json["result"]["hash"]
        .as_str()
        .expect("first block hash should be present")
        .to_string();

    let second_transaction = {
        let keypair = Keypair::from_bytes(&[1u8; 32]);

        let mut transaction = Transaction::new(
            1337,
            1,
            keypair.address(),
            [2u8; 20],
            500,
            21_000,
            1,
            Vec::new(),
        );

        transaction
            .sign(&keypair)
            .expect("second transaction should be signable");

        transaction
    };

    let second_body = transaction_json(&second_transaction);

    let second_submit = http_post(&address, "/transaction", &second_body);

    assert_http_status(&second_submit, "HTTP/1.1 200 OK");

    let second_block = http_post(&address, "/block/produce", "");

    assert_http_status(&second_block, "HTTP/1.1 200 OK");
    assert_json_contains(&second_block, "\"height\":2");

    let second_block_json: serde_json::Value =
        serde_json::from_str(second_block.split("\r\n\r\n").nth(1).unwrap())
            .expect("second block response should be valid JSON");

    let previous_hash = second_block_json["result"]["previous_hash"]
        .as_str()
        .expect("second block previous_hash should be present");

    assert_eq!(
        previous_hash, first_hash,
        "block #2 must reference block #1 hash"
    );
}

#[test]
fn rpc_get_account_returns_existing_account() {
    let address = start_rpc_server();

    let account_address = sender_address_hex();

    let response = http_get(&address, &format!("/state/{}", account_address));

    assert_http_status(&response, "HTTP/1.1 200 OK");

    assert_json_contains(&response, "\"status\":\"ok\"");
    assert_json_contains(&response, &format!("\"address\":\"{}\"", account_address));
    assert_json_contains(&response, "\"balance\":\"1000000\"");
    assert_json_contains(&response, "\"nonce\":0");
}

#[test]
fn rpc_get_account_returns_not_found_for_unknown_account() {
    let address = start_rpc_server();

    let account_address = "0909090909090909090909090909090909090909";

    let response = http_get(&address, &format!("/state/{}", account_address));

    assert_http_status(&response, "HTTP/1.1 404 NOT FOUND");
    assert_json_contains(&response, "\"code\":\"ACCOUNT_NOT_FOUND\"");
}

#[test]
fn rpc_get_account_rejects_invalid_address() {
    let address = start_rpc_server();

    let response = http_get(&address, "/state/not-a-valid-address");

    assert_http_status(&response, "HTTP/1.1 400 BAD REQUEST");
    assert_json_contains(&response, "\"code\":\"INVALID_ADDRESS\"");
}

#[test]
fn rpc_get_account_reflects_state_after_block_production() {
    let address = start_rpc_server();

    let sender_address = sender_address_hex();
    let recipient_address = "0202020202020202020202020202020202020202";

    let before = http_get(&address, &format!("/state/{}", sender_address));

    assert_http_status(&before, "HTTP/1.1 200 OK");
    assert_json_contains(&before, "\"balance\":\"1000000\"");
    assert_json_contains(&before, "\"nonce\":0");

    let transaction = signed_transaction();
    let body = transaction_json(&transaction);

    let submit = http_post(&address, "/transaction", &body);

    assert_http_status(&submit, "HTTP/1.1 200 OK");

    let produce = http_post(&address, "/block/produce", "");

    assert_http_status(&produce, "HTTP/1.1 200 OK");
    assert_json_contains(&produce, "\"height\":1");

    let sender_after = http_get(&address, &format!("/state/{}", sender_address));

    assert_http_status(&sender_after, "HTTP/1.1 200 OK");
    assert_json_contains(&sender_after, "\"balance\":\"978000\"");
    assert_json_contains(&sender_after, "\"nonce\":1");

    let recipient_after = http_get(&address, &format!("/state/{}", recipient_address));

    assert_http_status(&recipient_after, "HTTP/1.1 200 OK");
    assert_json_contains(&recipient_after, "\"balance\":\"1000\"");
    assert_json_contains(&recipient_after, "\"nonce\":0");
}
