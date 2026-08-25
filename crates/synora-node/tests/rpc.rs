use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
    time::Duration,
};

fn start_test_server() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind test port");

    let address = listener.local_addr().expect("get test address").to_string();

    drop(listener);

    address
}

fn http_get(address: &str, path: &str) -> String {
    let mut stream = TcpStream::connect(address).expect("connect to rpc server");

    let request = format!(
        "GET {} HTTP/1.1\r\n\
         Host: {}\r\n\
         Connection: close\r\n\
         \r\n",
        path, address
    );

    stream.write_all(request.as_bytes()).expect("write request");

    let mut response = String::new();

    stream.read_to_string(&mut response).expect("read response");

    response
}

#[allow(dead_code)]
fn http_post(address: &str, path: &str, body: &str) -> String {
    let mut stream = TcpStream::connect(address).expect("connect to rpc server");

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

    stream.write_all(request.as_bytes()).expect("write request");

    let mut response = String::new();

    stream.read_to_string(&mut response).expect("read response");

    response
}

#[test]
fn rpc_test_helpers_can_connect() {
    let address = start_test_server();

    let listener = TcpListener::bind(&address).expect("bind temporary server");

    thread::spawn(move || {
        if let Ok((mut stream, _)) = listener.accept() {
            let mut buffer = [0u8; 1024];

            let _ = stream.read(&mut buffer);

            let response = "HTTP/1.1 200 OK\r\n\
                 Content-Length: 2\r\n\
                 Connection: close\r\n\
                 \r\n\
                 OK";

            let _ = stream.write_all(response.as_bytes());
        }
    });

    thread::sleep(Duration::from_millis(10));

    let response = http_get(&address, "/");

    assert!(response.starts_with("HTTP/1.1 200 OK"));
    assert!(response.ends_with("OK"));
}
